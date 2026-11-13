"""HTTP adapter and bounded result normalization for MCP tools."""
import json
import stat
from datetime import datetime, timezone
from pathlib import Path
import httpx
from mcp.server.fastmcp.exceptions import ToolError


def fail(code, message, **details):
    raise ToolError(json.dumps({'code': code, 'message': message, 'details': details}, ensure_ascii=False))


def local_file(value, suffixes=None):
    path = Path(value)
    if not path.is_absolute() or str(path).startswith(('\\\\.\\', '\\\\?\\')):
        fail('invalid_path', 'Provide an absolute ordinary file path.')
    try:
        path = path.resolve(strict=True)
        if not stat.S_ISREG(path.stat().st_mode):
            fail('invalid_path', 'Path must be a regular file.')
    except FileNotFoundError:
        fail('file_not_found', 'File does not exist.', path=value)
    if suffixes and path.suffix.lower() not in suffixes:
        fail('unsupported_format', 'Unsupported file extension.', path=value)
    return path


def normalize_job(job):
    job = dict(job)
    for key in ('created', 'started', 'finished', 'now', 'scene_started', 'scene_finished'):
        if isinstance(job.get(key), (float, int)):
            job[key] = datetime.fromtimestamp(job[key], timezone.utc).isoformat()
    if job.get('stages'):
        job['stages'] = [dict(stage) for stage in job['stages']]
        for stage in job['stages']:
            for key in ('start', 'end', 'paused_at'):
                if isinstance(stage.get(key), (int, float)):
                    stage[key] = datetime.fromtimestamp(stage[key], timezone.utc).isoformat()
    if job.get('candidates'):
        job['candidates'] = [dict(index=i, **{k: c[k] for k in (
            'start', 'end', 'num_frames', 'preview_start', 'preview_end') if k in c},
            thumbnail_uri=f"flowhmr://jobs/{job['id']}/people/{i}") for i, c in enumerate(job['candidates'])]
    job['person_ids'] = ([p['person'] for p in job['scene']['people']] if job.get('scene')
                         else [job['selected_person']] if job.get('selected_person') is not None else [])
    job['available_formats'] = (['npz', 'vmd', 'vrma'] + (['fbx'] if job.get('has_fbx') else [])
                                if job.get('status') == 'done' else [])
    return job


class Backend:
    def __init__(self, url):
        self.url = url.rstrip('/')

    def client(self, timeout=30):
        return httpx.AsyncClient(base_url=self.url, timeout=timeout, trust_env=False)

    async def check(self, response):
        if response.is_success:
            return
        await response.aread()
        try:
            body = response.json()
        except ValueError:
            body = {}
        code = body.get('error_code', 'backend_http_error')
        if response.status_code == 404 and response.request.url.path.count('/') == 3:
            code = 'job_not_found'
        fail(code, body.get('error', 'Backend request failed.'),
             status=response.status_code, backend_code=body.get('error_code'), params=body.get('error_params', {}))

    async def request(self, method, path, **kwargs):
        try:
            async with self.client(120 if method != 'GET' else 30) as client:
                response = await client.request(method, path, **kwargs)
                await self.check(response)
                return response.json()
        except httpx.ConnectError:
            fail('backend_unavailable', 'FlowHMR backend is not reachable.')
        except httpx.TransportError as error:
            fail('outcome_unknown' if method != 'GET' else 'backend_unavailable',
                 'Transport failed; query jobs before resubmitting a write operation.', detail=str(error))

    async def job(self, job_id):
        return normalize_job(await self.request('GET', f'/api/jobs/{job_id}'))
