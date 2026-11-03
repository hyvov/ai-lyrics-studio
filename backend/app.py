"""Loopback-only API and static UI server. Run: python -m backend.app"""
from __future__ import annotations

import json
import math
import os
import secrets
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from backend.store import Conflict, Store, export_lrc, parse_lrc
from backend.jobs import RenderJobs
from backend.singing import fingerprint, composition_fingerprint
from contextlib import asynccontextmanager

ROOT = Path(__file__).resolve().parents[1]
MAX_AUDIO = 512 * 1024 * 1024
MAX_JSON = 4 * 1024 * 1024
EXTENSIONS = {'.wav', '.mp3', '.flac', '.m4a', '.ogg', '.aac', '.opus', '.webm'}


def create_app(data_root: Path | None = None) -> FastAPI:
    data = data_root or Path(os.environ.get('AI_LYRICS_DATA', ROOT / 'data'))
    store = Store(data / 'projects')
    assets = (data / 'assets').resolve()
    assets.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    jobs = RenderJobs(store, assets, data / 'jobs')
    @asynccontextmanager
    async def lifespan(_):
        yield
        jobs.shutdown()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    def view(p):
        for line in p['lines']:
            take = p.get('renders', {}).get(line['id'])
            valid = take and take['fingerprint'] == fingerprint(p, line)
            line['render_state'] = 'ready' if valid else ('stale' if take else 'pending')
            line['render_warning'] = ' '.join(take.get('warnings', [])) if valid else ''
        composite = p.get('composite', {})
        p['rendered_available'] = bool(composite.get('rendered') and composite.get('fingerprint') == composition_fingerprint(p))
        p['render_complete'] = all(x['text'] == x['original'] or x['render_state'] == 'ready' for x in p['lines'])
        return p

    @app.middleware('http')
    async def protect(request: Request, call_next):
        host = request.url.hostname
        if host not in ('127.0.0.1', 'localhost', '[::1]', '::1'):
            return JSONResponse({'error': '仅允许本机访问。'}, 403)
        origin = request.headers.get('origin')
        expected = str(request.base_url).rstrip('/')
        if origin and origin != expected:
            return JSONResponse({'error': '来源无效，请从应用页面操作。'}, 403)
        if request.method not in ('GET', 'HEAD'):
            supplied = request.headers.get('x-studio-token', '')
            if not secrets.compare_digest(supplied, token):
                return JSONResponse({'error': '会话已失效，请刷新页面。'}, 403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; media-src 'self' blob:; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; object-src 'none'"
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(ValueError)
    async def invalid(_, exc):
        return JSONResponse({'error': str(exc)}, 400)

    @app.exception_handler(FileNotFoundError)
    async def missing(_, exc):
        return JSONResponse({'error': '工程或媒体不存在。'}, 404)

    @app.exception_handler(Conflict)
    async def conflict(_, exc):
        return JSONResponse({'error': str(exc)}, 409)

    async def body(request):
        payload = bytearray()
        async for chunk in request.stream():
            payload.extend(chunk)
            if len(payload) > MAX_JSON:
                raise ValueError('请求数据过大。')
        try:
            value = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            raise ValueError('请求不是有效 JSON。')
        if not isinstance(value, dict):
            raise ValueError('请求须为 JSON 对象。')
        return value

    @app.get('/api/session')
    def session():
        return {'token': token, 'capabilities': {'storage': True, 'inference': jobs.runtime.status()['ready']}}

    @app.get('/api/projects')
    def projects():
        return [view(p) for p in store.list()]

    @app.post('/api/projects', status_code=201)
    async def create(request: Request):
        return view(store.create(await body(request)))

    @app.put('/api/projects/{project_id}')
    async def update(project_id: str, request: Request):
        return view(store.update(project_id, await body(request)))

    @app.post('/api/projects/{project_id}/lyrics')
    async def lyrics(project_id: str, request: Request):
        value = await body(request)
        p = store.get(project_id)
        if not isinstance(value.get('text'), str):
            raise ValueError('请提供 LRC 文本。')
        p['lines'] = parse_lrc(value['text'], p['duration'])
        p['revision'] = value.get('revision')
        return view(store.update(project_id, p))

    @app.get('/api/projects/{project_id}/lyrics.lrc')
    def export(project_id: str):
        return Response(export_lrc(store.get(project_id)), media_type='text/plain; charset=utf-8', headers={'Content-Disposition': 'attachment; filename="lyrics.lrc"'})

    @app.post('/api/projects/{project_id}/assets/{kind}')
    async def upload(project_id: str, kind: str, request: Request):
        if kind not in ('source', 'accompaniment'):
            raise ValueError('无效的轨道类型。')
        store.get(project_id)
        name = request.headers.get('x-file-name', '')
        from urllib.parse import unquote
        name = Path(unquote(name).replace('\\', '/')).name
        suffix = Path(name).suffix.lower()
        if suffix not in EXTENSIONS:
            raise ValueError('请选择支持的音频格式。')
        try:
            revision = int(request.headers.get('x-project-revision', ''))
            duration = float(request.headers.get('x-audio-duration', ''))
        except ValueError:
            raise ValueError('缺少有效的音频时长或工程版本。')
        if not math.isfinite(duration) or not 0 < duration <= 86400:
            raise ValueError('音频时长无效。')
        asset_id = uuid4().hex + suffix
        target = assets / asset_id
        temporary = assets / (asset_id + '.tmp')
        count = 0
        try:
            with temporary.open('wb') as handle:
                async for chunk in request.stream():
                    count += len(chunk)
                    if count > MAX_AUDIO:
                        raise ValueError('音频超过 512 MB 限制。')
                    handle.write(chunk)
            if not count:
                raise ValueError('音频文件为空。')
            temporary.replace(target)
            try:
                return view(store.attach(project_id, kind, {'id': asset_id, 'name': name, 'duration': duration}, revision, duration if kind == 'source' else None))
            except Exception:
                target.unlink(missing_ok=True)
                raise
        finally:
            temporary.unlink(missing_ok=True)

    @app.get('/api/runtime')
    def runtime():
        with jobs.lock:
            active = jobs.get(jobs.active) if jobs.active else None
        return {**jobs.runtime.status(), 'active_job': active}

    @app.get('/api/projects/{project_id}')
    def get_project(project_id: str):
        return view(store.get(project_id))

    @app.post('/api/projects/{project_id}/renders', status_code=202)
    async def render(project_id: str, request: Request):
        value = await body(request)
        return jobs.submit(project_id, value.get('revision'), value.get('segment_ids'))

    @app.get('/api/jobs/{job_id}')
    def get_job(job_id: str):
        return jobs.get(job_id)

    @app.post('/api/jobs/{job_id}/cancel')
    def cancel_job(job_id: str):
        return jobs.cancel(job_id)

    @app.get('/api/projects/{project_id}/exports/{kind}')
    def export_audio(project_id: str, kind: str):
        if kind not in ('rendered', 'mixed'):
            raise ValueError('请选择演唱干声或混音。')
        p = view(store.get(project_id))
        if not p['rendered_available'] or not p['render_complete']:
            raise Conflict('仍有改词句未生成或音频已过期，请先完成生成。')
        asset_id = p['composite'].get(kind)
        if not asset_id:
            raise ValueError('当前没有该导出文件，混音需要先导入伴奏再生成。')
        return FileResponse(assets / asset_id, filename=kind + '.wav', media_type='audio/wav')

    @app.get('/api/assets/{asset_id}')
    def media(asset_id: str):
        import re
        if not re.fullmatch(r'[a-f0-9]{32}\.(wav|mp3|flac|m4a|ogg|aac|opus|webm)', asset_id):
            raise FileNotFoundError()
        path = assets / asset_id
        if not path.is_file():
            raise FileNotFoundError()
        return FileResponse(path)

    app.mount('/', StaticFiles(directory=ROOT / 'design', html=True), name='ui')
    return app


if __name__ == '__main__':
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=18733)
    args = parser.parse_args()
    uvicorn.run(create_app(), host='127.0.0.1', port=args.port)
