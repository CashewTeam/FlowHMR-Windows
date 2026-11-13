"""Agent-facing workflows; all inference actions go through the existing backend."""
from typing import Any, Annotated, Literal
from functools import wraps
from pydantic import Field, RootModel
from mcp.server.fastmcp.tools import Tool
from mcp.types import ToolAnnotations
from .backend import fail, local_file, normalize_job
from .contract import ExportOptions, JobID, Parameters, People, Policy
from .characters import CharacterID, PosePatch


class Result(RootModel[dict[str, Any]]):
    pass


def build_tools(backend, exports):
    tools = []

    def register(read_only=False):
        def decorate(fn):
            @wraps(fn)
            async def tracked(*args, **kwargs):
                receipt = await backend.request('POST', '/api/mcp/activity',
                    json={'tool': fn.__name__, 'state': 'running'})
                try:
                    result = await fn(*args, **kwargs)
                except Exception as error:
                    await backend.request('POST', '/api/mcp/activity', json={
                        'id': receipt['id'], 'tool': fn.__name__, 'state': 'failed', 'error': str(error)[:1000]})
                    raise
                data = result.model_dump()
                details = {key: data[key] for key in ('job_id', 'export_id', 'path', 'character_id', 'id', 'status') if key in data}
                await backend.request('POST', '/api/mcp/activity', json={
                    'id': receipt['id'], 'tool': fn.__name__, 'state': 'done', 'details': details})
                return result
            tool = Tool.from_function(tracked, annotations=ToolAnnotations(readOnlyHint=read_only,
                destructiveHint=fn.__name__ == 'flowhmr_export_motion',
                idempotentHint=read_only, openWorldHint=False))
            model = tool.fn_metadata.arg_model
            model.model_config.update(extra='forbid', strict=True)
            model.model_rebuild(force=True)
            tool.parameters = model.model_json_schema()
            tools.append(tool)
            return fn
        return decorate

    @register(True)
    async def flowhmr_get_status() -> Result:
        """Check backend, runtime, model readiness and default generation parameters."""
        info = await backend.request('GET', '/api/info')
        return Result({'backend_ready': True, 'device': info['device'], 'runtime': info['runtime'],
                       'models_loaded': info['models_loaded'], 'defaults': info['defaults'],
                       'model_files': info['model_files']['items']})

    @register(True)
    async def flowhmr_get_parameter_schema() -> Result:
        """Get parameter defaults, strict schema, units, coordinate system and export options before submitting jobs."""
        return Result(await backend.request('GET', '/api/mcp/parameters'))

    @register()
    async def flowhmr_import_video(video_path: str, params: Parameters | None = None,
                                  selection_policy: Policy = 'explicit') -> Result:
        """Import a local absolute video path and queue generation. Returns job_id immediately.

        explicit waits for flowhmr_select_people; automatic authorizes the default person.
        Query flowhmr_get_job for progress. Do not resubmit on an uncertain transport outcome.
        """
        path = local_file(video_path, {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v'})
        if not 0 < path.stat().st_size <= 1024 ** 3:
            fail('video_size', 'Video must be nonempty and no larger than 1 GiB.')
        settings = params.model_dump(exclude_unset=True) if params else {}
        # Multipart encodes a null numeric parameter using the backend's documented null spelling.
        data = {key: 'null' if value is None else str(value).lower() if isinstance(value, bool) else str(value)
                for key, value in settings.items()}
        data.update(source='mcp', selection_policy=selection_policy)
        with path.open('rb') as video:
            created = await backend.request('POST', '/api/upload', data=data,
                                            files={'video': (path.name, video, 'application/octet-stream')})
        return Result(created)

    @register(True)
    async def flowhmr_list_jobs(limit: Annotated[int, Field(ge=1, le=100)] = 20,
                               cursor: str | None = None) -> Result:
        """List persisted jobs with pagination. Active jobs remain visible on the first page."""
        params = {'limit': limit}
        if cursor:
            params['before'] = cursor
        result = await backend.request('GET', '/api/jobs', params=params)
        result['jobs'] = [normalize_job(job) for job in result['jobs']]
        result.pop('now', None)
        return Result(result)

    @register(True)
    async def flowhmr_get_job(job_id: JobID) -> Result:
        """Get job progress, errors, effective parameters, candidate thumbnails and result person IDs.

        Candidate indices are zero-based. Export person_ids are provided separately.
        waiting_user requires selection; needs_alignment requires flowhmr_prepare_scene.
        """
        return Result(await backend.job(job_id))

    @register()
    async def flowhmr_select_people(job_id: JobID, candidate_indices: People) -> Result:
        """Confirm one or more current candidate indices and resume a waiting job. First GUI/Agent choice wins."""
        result = await backend.request('POST', f'/api/jobs/{job_id}/select', json={'people': candidate_indices})
        return Result({'job_id': job_id, 'accepted': True, 'candidate_indices': result['people']})

    @register()
    async def flowhmr_rerun_job(job_id: JobID, params: Parameters | None = None,
                                reselect_people: bool = False, selection_policy: Policy = 'explicit') -> Result:
        """Create a NEW job with changed parameters; omitted values inherit from the old job.

        Completed results remain intact. Compatible caches are reused by the backend.
        Set reselect_people=true to choose again. Active jobs cannot be rerun.
        """
        body = params.model_dump(exclude_unset=True) if params else {}
        body.update(source='mcp', reselect_person=reselect_people, selection_policy=selection_policy)
        result = await backend.request('POST', f'/api/jobs/{job_id}/rerun', json=body)
        return Result({**result, 'source_job_id': job_id})

    @register()
    async def flowhmr_cancel_job(job_id: JobID) -> Result:
        """Request cancellation. Query the job until cancelled/failed/done; acceptance is not completion."""
        await backend.request('POST', f'/api/jobs/{job_id}/cancel')
        return Result({'job_id': job_id, 'status': 'cancel_requested'})

    @register()
    async def flowhmr_prepare_scene(job_id: JobID) -> Result:
        """Queue alignment of a multi-person result when scene_needs_build=true; query job progress afterwards."""
        await backend.request('POST', f'/api/jobs/{job_id}/scene')
        return Result({'job_id': job_id, 'scene_state': 'queued'})

    @register()
    async def flowhmr_export_motion(options: ExportOptions) -> Result:
        """Queue a motion export to a local absolute path. Poll flowhmr_get_export(export_id).

        Arguments are nested under options. Parent directory must exist. No overwrite by default.
        Multi-person VMD/VRMA require .zip. FBX templates require skeleton only and no reference pose.
        NPZ exports the complete result. Only status=done proves a file was successfully published.
        """
        return Result(await exports.submit(options))

    @register(True)
    async def flowhmr_get_export(export_id: Annotated[str, Field(pattern=r'^[0-9a-f]{32}$')]) -> Result:
        """Get persisted export status, error, final file path, format and size. done means export completed."""
        return Result(exports.get(export_id))

    @register()
    async def flowhmr_import_character(model_path: str, name: str | None = None) -> Result:
        """Import a local PMX, VRM or FBX into the shared character library, including declared local textures.

        Returns character id and missing texture paths. This imports a preview character, not an inference body model.
        File and texture limits match the GUI library. Selection is a separate operation.
        """
        path = local_file(model_path, {'.pmx', '.vrm', '.fbx'})
        return Result(await backend.request('POST', '/api/mcp/characters/import',
                                            json={'file': str(path), 'name': name}))

    @register(True)
    async def flowhmr_list_characters() -> Result:
        """List shared preview character library entries (id, name, kind). Use ids for selection, not local paths."""
        catalog = await backend.request('GET', '/api/character-library')
        return Result({'characters': catalog['models']})

    @register(True)
    async def flowhmr_get_character_pose(job_id: JobID, person_id: Annotated[int, Field(ge=0)] | None = None) -> Result:
        """Read an actor's selected character, saved offsets, canonical bone controls and patch schema.

        Requires a completed result. Multi-person scenes require person_id from flowhmr_get_job.
        Controls are canonical preview adjustments, not raw model bone indices or animated keyframes.
        """
        return Result(await backend.request('GET', f'/api/mcp/jobs/{job_id}/character',
            params={'person': person_id} if person_id is not None else {}))

    @register()
    async def flowhmr_select_character(job_id: JobID, character_id: CharacterID | None,
                                       person_id: Annotated[int, Field(ge=0)] | None = None) -> Result:
        """Assign a library character to a completed actor. null restores the default SMPL-H preview.

        Reuses saved offsets for this actor/model. Changes persist and sync to the open preview.
        Generic motion exports do not bake these preview adjustments.
        """
        return Result(await backend.request('POST', f'/api/mcp/jobs/{job_id}/character',
            json={'person_id': person_id, 'character_id': character_id}))

    @register()
    async def flowhmr_update_character_pose(job_id: JobID, character_id: CharacterID, patch: PosePatch,
                                           person_id: Annotated[int, Field(ge=0)] | None = None) -> Result:
        """Patch saved static preview offsets; omitted controls keep their values.

        Query flowhmr_get_character_pose first for character_id and canonical bone IDs 0-36.
        Rotations: XYZ degrees [-180,180]; root translation: metres [-5,5]. Zero vectors reset controls.
        Finger controls 22-36 affect both hands. mirror is an editor preference, not automatic bilateral editing.
        IK applies only to PMX rigs that support native IK. Source animation and generic exports are unchanged.
        """
        return Result(await backend.request('PATCH', f'/api/mcp/jobs/{job_id}/character',
            json={'person_id': person_id, 'character_id': character_id,
                  'patch': patch.model_dump(exclude_unset=True)}))

    @register(True)
    async def flowhmr_get_viewer_snapshot() -> Result:
        """Capture the open UI's displayed frame and current preview skeletons on demand.

        Requires an open connected UI; returns an error if no UI responds. Bone transforms
        include live preview adjustments. Multiple open windows: first response wins.
        """
        return Result(await backend.request('GET', '/api/mcp/viewer-snapshot'))

    @register(True)
    async def flowhmr_get_motion_keyframes(job_id: JobID, start_frame: Annotated[int, Field(ge=0)] = 0,
                                          count: Annotated[int, Field(ge=1, le=120)] = 1,
                                          person_id: Annotated[int, Field(ge=0)] | None = None) -> Result:
        """Read up to 120 sampled source motion frames with SMPL-H indices and axis-angle rotations.

        Frame indices are zero-based and relative to the selected person's motion, not the UI timeline.
        These are dense per-frame samples, not authored sparse keyframes or adjusted character poses.
        Multi-person jobs require person_id from flowhmr_get_job.
        """
        params = {'start_frame': start_frame, 'count': count}
        if person_id is not None:
            params['person'] = person_id
        return Result(await backend.request('GET', f'/api/mcp/jobs/{job_id}/keyframes', params=params))

    @register()
    async def flowhmr_control_playback(job_id: JobID, action: Literal['seek', 'play', 'pause'],
                                       frame: Annotated[int, Field(ge=0)] | None = None) -> Result:
        """Control the open viewer of job_id; seek uses a zero-based result frame and pauses.

        seek requires frame; play/pause do not accept it. Query flowhmr_get_viewer_snapshot first.
        The first connected window claims the command. A timeout has an unknown outcome: inspect the UI
        before retrying. This controls preview playback, not source video or generated motion data.
        """
        if (action == 'seek') != (frame is not None):
            fail('invalid_playback_command', 'seek requires frame; play and pause do not accept frame.')
        return Result(await backend.request('POST', '/api/mcp/viewer-command',
                                           json={'job_id': job_id, 'action': action, 'frame': frame}))

    return tools
