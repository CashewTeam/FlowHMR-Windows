"""Real MCP and Flask HTTP calls with GPU work held in the existing queue."""
import asyncio
import ast
import importlib.util
import os
from pathlib import Path
import socket
import struct
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import numpy as np
from unittest.mock import patch, MagicMock

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
import uvicorn
from werkzeug.serving import make_server
from flowhmr.mcp.exports import Exports
from flowhmr.mcp.backend import Backend
from flowhmr.mcp.contract import ExportOptions, SERVICE_KEY_HEADER
from flowhmr.mcp.events import UIEvents
from flowhmr.character_library import CharacterLibrary
from flowhmr.character_files import CharacterFiles
from flowhmr.mcp import server as mcp_module

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('mcp_workflow_backend', ROOT / 'tools/web/server.py')
web = importlib.util.module_from_spec(spec)
spec.loader.exec_module(web)
SERVICE_HEADERS = {SERVICE_KEY_HEADER: web.MCP_SERVICE.service_key}


class ExportSafetyTest(unittest.TestCase):
    def test_target_created_after_submission_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'motion.npz'
            def handler(request):
                if request.url.path.endswith('/npz'):
                    return httpx.Response(200, content=b'new-motion')
                return httpx.Response(200, json={'id': 'job', 'status': 'done', 'selected_person': 0})
            backend = Backend('http://backend')
            backend.client = lambda timeout=30: httpx.AsyncClient(
                base_url=backend.url, transport=httpx.MockTransport(handler))
            exports = Exports(root / 'receipts', backend)
            async def run():
                receipt = await exports.submit(ExportOptions(job_id='job', format='npz', output_path=str(target)))
                target.write_bytes(b'existing-user-file')
                await exports.close()
                self.assertEqual(exports.get(receipt['export_id'])['error']['code'], 'output_exists')
            asyncio.run(run())
            self.assertEqual(target.read_bytes(), b'existing-user-file')
            self.assertFalse(list(root.glob('.flowhmr-export-*.tmp')))

    def test_recovery_marks_interrupted_and_removes_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            temporary = root / '.flowhmr-export-test.tmp'
            temporary.write_bytes(b'partial')
            exports = Exports(root / 'receipts', None)
            exports.save({'export_id': 'a' * 32, 'status': 'running', 'temporary_path': str(temporary)})
            recovered = Exports(root / 'receipts', None)
            recovered.recover()
            self.assertEqual(recovered.get('a' * 32)['status'], 'failed')
            self.assertFalse(temporary.exists())


class WorkflowsTest(unittest.TestCase):
    def test_http_routes_require_their_own_local_credentials(self):
        with patch.dict(os.environ, {web.CHARACTER_KEY_ENV: 'desktop-test-key'}), web.app.test_client() as client:
            desktop_headers = {'X-FlowHMR-Desktop-Key': 'desktop-test-key'}
            self.assertEqual(client.get('/api/mcp', headers=SERVICE_HEADERS).status_code, 403)
            self.assertEqual(client.get('/api/mcp/activity').status_code, 403)
            self.assertEqual(client.get('/api/mcp', headers={**desktop_headers, 'Sec-Fetch-Site': 'cross-site'}).status_code, 403)
            self.assertEqual(client.get('/api/mcp', headers=desktop_headers).headers['Cache-Control'], 'no-store')
            self.assertEqual(client.get('/api/mcp/parameters', headers=desktop_headers).status_code, 403)
            schema = client.get('/api/mcp/parameters', headers=SERVICE_HEADERS).get_json()
            self.assertEqual(set(schema['defaults']), set(schema['parameters']['properties']))
            self.assertEqual(schema['parameters']['properties']['sam_hand_stride']['enum'], [1, 2, 4])
            self.assertEqual(client.post('/api/mcp/characters/import', json={}).status_code, 403)

    def test_source_joint_names_match_original_smplh_order(self):
        from flowhmr.mcp.joint_names import SMPLH_JOINT_NAMES_52
        source = (ROOT / 'flowhmr/core/bodymodels/smpl_skeleton.py').read_text(encoding='utf-8')
        assignment = next(node for node in ast.parse(source).body if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name) and target.id == 'SMPLH_JOINT_NAMES_52'
                                  for target in node.targets))
        self.assertEqual(SMPLH_JOINT_NAMES_52, ast.literal_eval(assignment.value))

    def test_playback_command_validation_claim_and_reply(self):
        events = UIEvents()
        responses = []
        with patch.object(web, 'UI_EVENTS', events):
            def send():
                with web.app.test_client() as client:
                    responses.append(client.post('/api/mcp/viewer-command', headers=SERVICE_HEADERS, json={
                        'job_id': 'job1', 'action': 'seek', 'frame': 5}))
            thread = threading.Thread(target=send)
            thread.start()
            event = events.wait(0, timeout=1)[0]
            self.assertEqual(event['data']['command']['frame'], 5)
            token = event['data']['request_id']
            with web.app.test_client() as client:
                path = f'/api/ui/viewer-command/{token}'
                self.assertEqual(client.post(path + '/claim').status_code, 200)
                self.assertEqual(client.post(path + '/claim').status_code, 410)
                self.assertEqual(client.post(path + '/reply', json={'frame': 5}).status_code, 200)
                self.assertEqual(client.post('/api/mcp/viewer-command', headers=SERVICE_HEADERS, json={
                    'job_id': 'job1', 'action': 'seek', 'frame': True}).status_code, 400)
            thread.join(2)
            self.assertEqual(responses[0].get_json()['frame'], 5)

    def test_snapshot_accepts_large_bone_data_and_rejects_expired_reply(self):
        events = UIEvents()
        result = []
        with patch.object(web, 'UI_EVENTS', events), web.app.test_client() as client:
            thread = threading.Thread(target=lambda: result.append(events.request_snapshot(timeout=1)))
            thread.start()
            token = events.wait(0, timeout=1)[0]['data']['request_id']
            payload = {'bones': [{'name': 'bone' * 100}] * 100}
            self.assertEqual(client.post(f'/api/ui/viewer-snapshot/{token}', json=payload).status_code, 200)
            thread.join(2)
            self.assertEqual(result, [payload])
            self.assertEqual(client.post(f'/api/ui/viewer-snapshot/{token}', json=payload).status_code, 410)

    def test_source_keyframe_range_and_indices(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            actor = root / 'job1'
            actor.mkdir()
            poses = np.arange(3 * 52 * 3, dtype=np.float32).reshape(3, 52, 3)
            np.savez(actor / 'motion.npz', poses=poses, trans=np.zeros((3, 3)), mocap_framerate=30)
            job = {'id': 'job1', 'status': 'done', 'selected_person': 0}
            with patch.object(web, 'ARGS', SimpleNamespace(work_dir=directory)), \
                    patch.object(web, 'JOBS', {'job1': job}), patch.object(web, 'MEMBER_DIRS', {}), \
                    web.app.test_client() as client:
                response = client.get('/api/mcp/jobs/job1/keyframes?start_frame=1&count=2&person=0', headers=SERVICE_HEADERS)
                self.assertEqual(response.status_code, 200)
                result = response.get_json()
                self.assertEqual(result['joints'][16], {'index': 16, 'name': 'left_shoulder'})
                self.assertEqual(result['frames'][0]['rotations_axis_angle'][16], poses[1, 16].tolist())
                self.assertEqual(result['frames'][1]['frame'], 2)
                self.assertEqual(client.get('/api/mcp/jobs/job1/keyframes?start_frame=2&count=2', headers=SERVICE_HEADERS).status_code, 400)

    def test_sse_delivery_replay_and_origin_check(self):
        events = UIEvents()
        with patch.object(web, 'UI_EVENTS', events), web.app.test_client() as client:
            self.assertEqual(client.get('/api/ui/events', headers={'Origin':'https://evil.example'}).status_code,403)
            self.assertEqual(client.get('/api/ui/events', headers={'Last-Event-ID':'bad'}).status_code,400)
            response = client.get('/api/ui/events', buffered=False)
            self.assertIn(b'event: connected', next(response.response))
            sent = events.publish('jobs', {'job_id':'test'})
            packet = next(response.response)
            self.assertIn(b'event: update',packet)
            self.assertIn(b'"job_id": "test"',packet)
            response.close()
            replay = client.get('/api/ui/events', headers={'Last-Event-ID':'0'}, buffered=False)
            next(replay.response)
            self.assertIn(f"id: {sent['id']}".encode(), next(replay.response))
            replay.close()

    def test_explicit_selection_timeout_never_selects_default(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(web, 'ARGS', SimpleNamespace(work_dir=directory, device='cuda')), \
                    patch.object(web, 'JOBS', {}), patch.object(web, 'SELECTIONS', {}), \
                    patch.object(web, 'SELECT_TIMEOUT_S', 0), patch.object(web, 'save_person_thumbnails'):
                params = web.default_params()
                params.update(postprocess=False, hamer=False)
                job = web.new_job('clip.mp4', params, '.mp4', source='mcp', selection_policy='explicit')
                engine = MagicMock()
                def process(*args, **kwargs):
                    kwargs['person_selector'](SimpleNamespace(width=100, height=100),
                        [{'track_index': 0, 'start': 0, 'end': 10, 'num_frames': 11}], 0)
                engine.process_video.side_effect = process
                with patch.object(web, 'get_engine', return_value=engine), patch.object(web, '_confirm_people') as confirm:
                    with self.assertRaises(web.UserMessage) as error:
                        web.run_job(job['id'])
                    self.assertEqual(error.exception.code, 'selection_timeout')
                    confirm.assert_not_called()
                    self.assertEqual(web.SELECTIONS, {})

    def test_job_and_export_workflows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / 'clip.mp4'
            video.write_bytes(b'test-upload' * 2048)
            web.ARGS = SimpleNamespace(work_dir=directory, device='cuda', ckpt=None, model_cfg=None)
            web.JOBS = {}
            web.MEMBER_DIRS = {}
            backend = make_server('127.0.0.1', 0, web.app, threaded=True)
            backend_thread = threading.Thread(target=backend.serve_forever, daemon=True)
            backend_thread.start()
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
            app = mcp_module.create_app(f'http://127.0.0.1:{backend.server_port}', port,
                                        export_directory=root / 'exports', service_key=web.MCP_SERVICE.service_key)
            service = uvicorn.Server(uvicorn.Config(app, log_level='error'))
            thread = threading.Thread(target=service.run, kwargs={'sockets': [sock]}, daemon=True)
            with patch.object(web.RUNTIME, 'summary', return_value={'ready': True, 'pending_restart': False}), \
                    patch.object(web, 'enqueue') as enqueue, \
                    patch.object(web, 'CHARACTER_LIBRARY', CharacterLibrary(root / 'characters')), \
                    patch.object(web, 'CHARACTER_FILES', CharacterFiles()), \
                    patch.dict(os.environ, {web.CHARACTER_KEY_ENV: 'desktop-test-key'}), \
                    patch.object(Exports, 'recover', autospec=True, side_effect=Exports.recover) as recovery:
                thread.start()
                try:
                    deadline = time.monotonic() + 5
                    while not service.started:
                        if time.monotonic() > deadline:
                            self.fail('MCP startup timed out')
                        time.sleep(0.01)

                    async def run():
                        async with httpx.AsyncClient(trust_env=False) as http:
                            async with streamable_http_client(f'http://127.0.0.1:{port}/mcp', http_client=http) as (read, write, _):
                                async with ClientSession(read, write) as session:
                                    await session.initialize()

                                    async def call(name, arguments, error=False):
                                        result = await session.call_tool('flowhmr_' + name, arguments)
                                        self.assertEqual(result.isError, error, str(result.content))
                                        return result.structuredContent

                                    schema = await call('get_parameter_schema', {})
                                    self.assertIn('steps', schema['parameters']['properties'])
                                    await call('import_video', {'video_path': str(video), 'params': {'steps': 0}}, True)
                                    await call('import_video', {'video_path': str(video), 'typo': True}, True)
                                    for invalid in (3, True, 2.0):
                                        await call('import_video', {'video_path': str(video), 'params': {'sam_hand_stride': invalid}}, True)
                                    self.assertEqual(enqueue.call_count, 0)
                                    created = await call('import_video', {'video_path': str(video), 'params': {'steps': 3, 'sam_hand_stride': 2}})
                                    job_id = created['job_id']
                                    self.assertEqual(created['params']['steps'], 3)
                                    self.assertEqual(created['params']['sam_hand_stride'], 2)
                                    self.assertEqual(created['selection_policy'], 'explicit')
                                    job = web.JOBS[job_id]
                                    self.assertEqual(job['source'], 'mcp')
                                    self.assertEqual((root / job_id / 'input.mp4').read_bytes(), video.read_bytes())
                                    listed = await call('list_jobs', {'limit': 1})
                                    self.assertEqual(listed['jobs'][0]['id'], job_id)
                                    job.update(status='waiting_user', candidates=[{'start': 0, 'end': 9, 'num_frames': 10}])
                                    slot = {'event': threading.Event(), 'people': None}
                                    web.SELECTIONS[job_id] = slot
                                    person_dir = root / job_id / 'persons'
                                    person_dir.mkdir()
                                    (person_dir / '0.jpg').write_bytes(b'jpeg-test')
                                    detail = await call('get_job', {'job_id': job_id})
                                    resource = await session.read_resource(detail['candidates'][0]['thumbnail_uri'])
                                    self.assertTrue(resource.contents)
                                    await call('select_people', {'job_id': job_id, 'candidate_indices': [3]}, True)
                                    await call('select_people', {'job_id': job_id, 'candidate_indices': [0]})
                                    self.assertTrue(slot['event'].is_set())
                                    await call('select_people', {'job_id': job_id, 'candidate_indices': [0]}, True)
                                    job.update(status='done', selected_person=None, candidates=None)
                                    self.assertEqual((await call('get_job', {'job_id': job_id}))['person_ids'], [0])
                                    texture = root / '肌肤.png'
                                    texture.write_bytes(b'fake-texture')
                                    model = root / 'character.pmx'
                                    def pmx_text(value):
                                        data = value.encode('utf-8')
                                        return struct.pack('<i', len(data)) + data
                                    model.write_bytes(b'PMX ' + struct.pack('<fB', 2.0, 8) + bytes([1,0,1,1,1,1,1,1]) +
                                        pmx_text('test') + pmx_text('') * 3 + struct.pack('<iii',0,0,1) + pmx_text(texture.name))
                                    imported = await call('import_character', {'model_path': str(model), 'name': 'Test Character'})
                                    character_id = imported['id']
                                    self.assertEqual(imported['missing_textures'], [])
                                    self.assertEqual((root / 'characters' / character_id / 'files' / texture.name).read_bytes(), texture.read_bytes())
                                    characters = await call('list_characters', {})
                                    self.assertEqual(characters['characters'][0]['id'], character_id)
                                    selected = await call('select_character', {'job_id': job_id, 'character_id': character_id})
                                    self.assertEqual(selected['pose']['rotations'], {})
                                    edited = await call('update_character_pose', {'job_id': job_id, 'character_id': character_id,
                                        'patch': {'rotations': {'16': [10,0,-5]}, 'root_translation': [0,0.1,0]}})
                                    self.assertEqual(edited['pose']['rotations']['16'], [10,0,-5])
                                    await call('update_character_pose', {'job_id': job_id, 'character_id': character_id,
                                        'patch': {'root_rotation': [0,20,0]}})
                                    pose = await call('get_character_pose', {'job_id': job_id})
                                    self.assertEqual(pose['pose']['rotations']['16'], [10,0,-5])
                                    self.assertEqual(len(pose['bones']),37)
                                    await call('update_character_pose', {'job_id': job_id, 'character_id': character_id,
                                        'patch': {'rotations': {'16':[181,0,0]}}}, True)
                                    await call('update_character_pose', {'job_id': job_id, 'character_id': character_id,
                                        'patch': {'rotations': {'52':[0,0,0]}}}, True)
                                    await call('select_character', {'job_id': job_id, 'character_id': None})
                                    await call('update_character_pose', {'job_id': job_id, 'character_id': character_id,
                                        'patch': {'rotations': {'16':[0,0,0]}}}, True)
                                    motion = root / job_id / 'motion.npz'
                                    motion.write_bytes(b'npz-download-test')
                                    rerun = await call('rerun_job', {'job_id': job_id, 'params': {'steps': 4, 'sam_hand_stride': 4}})
                                    self.assertNotEqual(rerun['job_id'], job_id)
                                    self.assertEqual(rerun['params']['steps'], 4)
                                    self.assertEqual(rerun['params']['sam_hand_stride'], 4)
                                    await call('cancel_job', {'job_id': rerun['job_id']})
                                    self.assertEqual(web.JOBS[rerun['job_id']]['status'], 'cancel_requested')
                                    target = root / 'output.npz'
                                    exported = await call('export_motion', {'options': {'job_id': job_id,
                                        'format': 'npz', 'output_path': str(target)}})
                                    for _ in range(100):
                                        receipt = await call('get_export', {'export_id': exported['export_id']})
                                        if receipt['status'] in ('done', 'failed'):
                                            break
                                        await asyncio.sleep(0.02)
                                    self.assertEqual(receipt['status'], 'done', receipt)
                                    self.assertEqual(target.read_bytes(), motion.read_bytes())
                                    await call('export_motion', {'options': {'job_id': job_id,
                                        'format': 'npz', 'output_path': str(target)}}, True)
                                    self.assertEqual(recovery.call_count, 1)
                                    recovered = Exports(root / 'exports', None)
                                    recovered.recover()
                                    self.assertEqual(recovered.get(exported['export_id'])['status'], 'done')
                                    motion.unlink()
                                    failed_target = root / 'failed.npz'
                                    failed = await call('export_motion', {'options': {'job_id': job_id,
                                        'format': 'npz', 'output_path': str(failed_target)}})
                                    for _ in range(100):
                                        receipt = await call('get_export', {'export_id': failed['export_id']})
                                        if receipt['status'] in ('done', 'failed'):
                                            break
                                        await asyncio.sleep(0.02)
                                    self.assertEqual(receipt['status'], 'failed')
                                    self.assertFalse(failed_target.exists())
                                    self.assertEqual(list(root.glob('.flowhmr-export-*.tmp')), [])
                                    job.update(person_batch=job_id, scene_state='pending', progress=100, selected_person=0)
                                    child = web.new_job('clip.mp4', job['params'], '.mp4',
                                        person_batch=job_id, source='mcp', status='done', selected_person=1)
                                    detail = await call('get_job', {'job_id': job_id})
                                    self.assertTrue(detail['scene_needs_build'])
                                    prepared = await call('prepare_scene', {'job_id': job_id})
                                    self.assertEqual(prepared['scene_state'], 'queued')
                                    self.assertEqual(web.JOB_QUEUE.get_nowait(), ('scene', job_id))
                                    web.JOB_QUEUE.task_done()
                                    await call('select_character', {'job_id': job_id, 'person_id': 1, 'character_id': character_id})
                                    second = await call('select_character', {'job_id': job_id, 'person_id': 2, 'character_id': character_id})
                                    second_pose = second['pose']
                                    await call('update_character_pose', {'job_id': job_id, 'person_id': 1,
                                        'character_id': character_id, 'patch': {'root_translation':[0,1,0]}})
                                    unchanged = await call('get_character_pose', {'job_id': job_id, 'person_id': 2})
                                    self.assertEqual(unchanged['pose'], second_pose)
                                    await call('get_character_pose', {'job_id': job_id}, True)
                                    async with httpx.AsyncClient(trust_env=False) as audit_client:
                                        audit = (await audit_client.get(
                                            f'http://127.0.0.1:{backend.server_port}/api/mcp/activity',
                                            headers={'X-FlowHMR-Desktop-Key': 'desktop-test-key'})).json()
                                    self.assertTrue(any(row['tool']=='flowhmr_import_video' and row['state']=='done'
                                                        for row in audit['operations']))
                                    self.assertTrue(any(row['tool']=='flowhmr_update_character_pose' and row['state']=='failed'
                                                        for row in audit['operations']))
                    asyncio.run(run())
                finally:
                    service.should_exit = True
                    thread.join(5)
                    backend.shutdown()
                    backend.server_close()
                    backend_thread.join(5)
                    sock.close()
                    web.SELECTIONS.clear()
                    web.CANCEL_REQUESTS.clear()
