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

from backend.store import Conflict, Store, export_lrc, export_srt, parse_lyrics,snapshot_lyrics,validate_project
from backend.jobs import RenderJobs
from backend.media import source_issue,waveform
from backend.singing import fingerprint, composition_fingerprint, svc_fingerprint, units
from backend.timeline import alignment_fingerprint, cached_alignment, validate_timings
from backend.phrases import split_phrase, merge_next
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
        p.pop('lyric_history',None)
        p['source_issue'] = source_issue(p, assets)
        for line in p['lines']:
            alignment = cached_alignment(p,line)
            line['unit_timings'] = alignment['unit_timings'] if alignment else []
            line['alignment_engine'] = alignment.get('engine') if alignment else None
            line['alignment_state'] = 'ready' if alignment else 'pending'
            take = p.get('renders', {}).get(line['id'])
            valid = take and take['fingerprint'] == fingerprint(p, line)
            line['render_state'] = 'ready' if valid else ('stale' if take else 'pending')
            line['render_warning'] = ' '.join(take.get('warnings', [])) if valid else ''
            line['render_legacy'] = bool(valid and line['text'] != line['original'] and not take.get('reference_safe'))
            if line['render_legacy']:
                line['render_warning'] = '旧版参考音频可能导致原词残留，请重新生成。'
        composite = p.get('composite', {})
        p['rendered_available'] = bool(composite.get('rendered') and composite.get('fingerprint') == composition_fingerprint(p))
        svc=p.get('svc',{})
        p['svc_available'] = bool(svc.get('asset_id') and svc.get('target_kind') in ('source','rendered')
            and svc.get('fingerprint')==svc_fingerprint(p,svc['target_kind'])
            and (svc['target_kind']!='rendered' or p['rendered_available']))
        p['svc_mixed_available'] = bool(p['svc_available'] and svc.get('mixed_asset_id') and svc.get('accompaniment_id')
            and svc['accompaniment_id']==p.get('assets',{}).get('accompaniment',{}).get('id'))
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
            raise ValueError('请提供 SRT 或 LRC 文本。')
        lines = parse_lyrics(value['text'], p['duration'], value.get('format'))
        return view(store.replace_lyrics(project_id,value.get('revision'),lines,'导入字幕或歌词前'))

    @app.get('/api/projects/{project_id}/lyric-history')
    def lyric_history(project_id: str):
        p=store.get(project_id);source=p.get('assets',{}).get('source',{}).get('id')
        return {'history':[{'id':x['id'],'created_at':x['created_at'],'reason':x['reason'],
            'count':len(x['lines']),'preview':' / '.join(line['text'] for line in x['lines'][:2]),
            'compatible':x.get('source_id')==source} for x in reversed(p.get('lyric_history',[]))]}

    @app.post('/api/projects/{project_id}/lyric-history/{snapshot_id}/restore')
    async def restore_lyrics(project_id: str,snapshot_id: str,request: Request):
        value=await body(request)
        with jobs.lock,store.lock:
            if jobs.active:raise Conflict('当前有任务运行，请等待完成或取消后再恢复。')
            p=store.get(project_id)
            if type(value.get('revision')) is not int or value['revision']!=p['revision']:
                raise Conflict('工程已更新，请刷新后再恢复歌词。')
            saved=next((x for x in p.get('lyric_history',[]) if x['id']==snapshot_id),None)
            if not saved:raise FileNotFoundError()
            if saved.get('source_id')!=p.get('assets',{}).get('source',{}).get('id'):
                raise Conflict('该版本对应的原唱音频已更换，不能直接恢复。')
            validate_project({**p,'lines':saved['lines']})
            snapshot_lyrics(p,'恢复历史歌词前')
            p['lines']=saved['lines'];p['revision']+=1
            return view(store._write(p))

    @app.get('/api/projects/{project_id}/lyrics.lrc')
    def export(project_id: str):
        return Response(export_lrc(store.get(project_id)), media_type='text/plain; charset=utf-8', headers={'Content-Disposition': 'attachment; filename="lyrics.lrc"'})

    @app.get('/api/projects/{project_id}/lyrics.srt')
    def export_subtitles(project_id: str):
        return Response(export_srt(store.get(project_id)), media_type='application/x-subrip; charset=utf-8', headers={'Content-Disposition': 'attachment; filename="lyrics.srt"'})

    @app.post('/api/projects/{project_id}/assets/{kind}')
    async def upload(project_id: str, kind: str, request: Request):
        if kind not in ('source', 'accompaniment', 'song', 'reference', 'reference_pending'):
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
        return {**jobs.runtime.status(), 'separation_ready': bool(jobs.runtime.separation_config()),
                'transcription_ready': bool(jobs.runtime.transcription_config()), 'svc_ready':bool(jobs.runtime.svc_config()), 'ying_ready':bool(jobs.runtime.ying_config()), 'active_job': active}

    @app.get('/api/projects/{project_id}')
    def get_project(project_id: str):
        return view(store.get(project_id))

    @app.post('/api/projects/{project_id}/renders', status_code=202)
    async def render(project_id: str, request: Request):
        value = await body(request)
        return jobs.submit(project_id, value.get('revision'), value.get('segment_ids'))

    @app.post('/api/projects/{project_id}/alignments', status_code=202)
    async def align(project_id: str, request: Request):
        value = await body(request)
        return jobs.submit(project_id,value.get('revision'),value.get('segment_ids'),kind='align')

    @app.post('/api/projects/{project_id}/separation', status_code=202)
    async def separate_song(project_id: str, request: Request):
        value = await body(request)
        return jobs.submit_separation(project_id, value.get('revision'))

    @app.post('/api/projects/{project_id}/transcription', status_code=202)
    async def transcribe_song(project_id: str, request: Request):
        value = await body(request)
        return jobs.submit_transcription(project_id, value.get('revision'), value.get('replace') is True)

    @app.post('/api/projects/{project_id}/svc', status_code=202)
    async def convert_voice(project_id: str, request: Request):
        value=await body(request)
        return jobs.submit_svc(project_id,value.get('revision'),value.get('target_kind'))

    @app.post('/api/projects/{project_id}/compose', status_code=202)
    async def compose_audio(project_id: str, request: Request):
        value=await body(request)
        return jobs.submit_compose(project_id,value.get('revision'))

    @app.put('/api/projects/{project_id}/segments/{segment_id}/phrase')
    async def edit_phrase(project_id: str, segment_id: str, request: Request):
        """Save a phrase and any adjacent boundaries as one revision."""
        value = await body(request)
        with store.lock:
            p = store.get(project_id)
            if type(value.get('revision')) is not int or value['revision'] != p['revision']:
                raise Conflict('工程已更新，请重新打开歌词编辑。')
            index = next((i for i, line in enumerate(p['lines']) if line['id'] == segment_id), -1)
            if index < 0:
                raise FileNotFoundError()
            start, end = value.get('start'), value.get('end')
            if any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in (start, end)):
                raise ValueError('唱句边界须为有限数字。')
            if start < 0 or end > p['duration'] or end - start < .12 - 1e-9:
                raise ValueError('唱句须在音频范围内且至少保留 120 毫秒。')
            line = p['lines'][index]
            previous = p['lines'][index - 1] if index else None
            following = p['lines'][index + 1] if index + 1 < len(p['lines']) else None
            if previous and start < previous['end']:
                if start - previous['start'] < .12 - 1e-9:
                    raise ValueError('调整后上一句至少需要保留 120 毫秒。')
                previous['end'] = start
            if following and end > following['start']:
                if following['end'] - end < .12 - 1e-9:
                    raise ValueError('调整后下一句至少需要保留 120 毫秒。')
                following['start'] = end
            if 'generation_params' in value:
                line['generation_params'] = value['generation_params']
            elif value.get('mode') != line.get('mode'):
                line.pop('generation_params', None)
            line.update(start=start, end=end, text=value.get('text'), mode=value.get('mode'), restore=value.get('restore'))
            return view(store.update(project_id, p))

    @app.post('/api/projects/{project_id}/segments/{segment_id}/structure')
    async def edit_phrase_structure(project_id: str, segment_id: str, request: Request):
        value=await body(request)
        with store.lock:
            p=store.get(project_id)
            if type(value.get('revision')) is not int or value['revision']!=p['revision']:
                raise Conflict('工程已更新，请重新打开歌词编辑。')
            if value.get('action')=='split':
                split_phrase(p,segment_id,value.get('position'),value.get('time'))
            elif value.get('action')=='merge':
                merge_next(p,segment_id)
            else:
                raise ValueError('请选择拆分或合并。')
            return view(store.update(project_id,p))

    @app.get('/api/projects/{project_id}/segments/{segment_id}/takes')
    def list_takes(project_id: str, segment_id: str):
        p=store.get(project_id)
        line=next((x for x in p['lines'] if x['id']==segment_id),None)
        if line is None:raise FileNotFoundError()
        current=p.get('renders',{}).get(segment_id,{})
        takes=[]
        for take in reversed(p.get('render_history',[])):
            if take.get('segment_id')!=segment_id:continue
            asset_id=take.get('asset_id','')
            import re
            if not re.fullmatch(r'[a-f0-9]{32}\.wav',asset_id) or not (assets/asset_id).is_file():continue
            takes.append({'asset_id':asset_id,'created_at':take.get('created_at'),
                'lyrics':take.get('lyrics'),'mode':take.get('mode'),'warnings':take.get('warnings',[]),
                'generation_params':take.get('generation_params'),'seed_used':take.get('seed_used'),
                'current':asset_id==current.get('asset_id'),'compatible':take.get('fingerprint')==fingerprint(p,line)})
            if len(takes)>=100:break
        return {'takes':takes}

    @app.post('/api/projects/{project_id}/segments/{segment_id}/takes/{asset_id}/activate')
    async def activate_take(project_id: str, segment_id: str, asset_id: str, request: Request):
        import re
        value=await body(request)
        if not re.fullmatch(r'[a-f0-9]{32}\.wav',asset_id):raise FileNotFoundError()
        with jobs.lock,store.lock:
            if jobs.active:raise Conflict('当前有任务运行，请等待完成或取消后再切换版本。')
            p=store.get(project_id)
            if type(value.get('revision')) is not int or value['revision']!=p['revision']:
                raise Conflict('工程已更新，请重新打开历史版本。')
            line=next((x for x in p['lines'] if x['id']==segment_id),None)
            if line is None:raise FileNotFoundError()
            take=next((x for x in reversed(p.get('render_history',[])) if x.get('segment_id')==segment_id and x.get('asset_id')==asset_id),None)
            if not take or not (assets/asset_id).is_file():raise FileNotFoundError()
            if take.get('fingerprint')!=fingerprint(p,line):
                raise Conflict('该版本对应的原词、新词、边界或素材已变化，不能直接套用。')
            if p.get('renders',{}).get(segment_id,{}).get('asset_id')==asset_id:return view(p)
            p.setdefault('renders',{})[segment_id]={k:take[k] for k in ('asset_id','fingerprint','job_id','warnings') if k in take}
            p.pop('composite',None)
            p['revision']+=1
            return view(store._write(p))

    @app.put('/api/projects/{project_id}/segments/{segment_id}/timeline')
    async def edit_word_times(project_id: str, segment_id: str, request: Request):
        value = await body(request)
        with store.lock:
            p = store.get(project_id)
            if type(value.get('revision')) is not int or value['revision'] != p['revision']:
                raise Conflict('工程已更新，请重新打开时间轴。')
            line = next((x for x in p['lines'] if x['id'] == segment_id), None)
            if line is None:
                raise FileNotFoundError()
            timings = validate_timings(line,value.get('unit_timings'))
            p.setdefault('alignments',{})[segment_id] = {'fingerprint':alignment_fingerprint(p,line),
                'engine':'manual','unit_timings':timings}
            p['revision'] += 1
            return view(store._write(p))

    @app.put('/api/projects/{project_id}/segments/{segment_id}/original')
    async def correct_original(project_id: str, segment_id: str, request: Request):
        value = await body(request)
        with store.lock:
            p = store.get(project_id)
            if type(value.get('revision')) is not int or value['revision'] != p['revision']:
                raise Conflict('工程已更新，请重新打开时间轴。')
            line = next((x for x in p['lines'] if x['id'] == segment_id),None)
            if line is None:
                raise FileNotFoundError()
            text = value.get('text')
            if not isinstance(text,str) or not text.strip():
                raise ValueError('原唱实际唱词不能为空。')
            text = text.strip(); units(text)
            if line['text'] == line['original']:
                line['text'] = text
            line['original'] = text
            line['needs_review'] = False
            return view(store.update(project_id,p))

    @app.get('/api/jobs/{job_id}')
    def get_job(job_id: str):
        return jobs.get(job_id)

    @app.post('/api/jobs/{job_id}/cancel')
    def cancel_job(job_id: str):
        return jobs.cancel(job_id)

    @app.get('/api/projects/{project_id}/exports/{kind}')
    def export_audio(project_id: str, kind: str):
        if kind not in ('rendered', 'mixed', 'svc', 'svc_mixed'):
            raise ValueError('请选择演唱干声或混音。')
        p = view(store.get(project_id))
        if kind in ('svc','svc_mixed'):
            if not p['svc_available' if kind=='svc' else 'svc_mixed_available']:raise Conflict('音色转换结果不存在或已经过期。')
            asset_id=p['svc']['asset_id' if kind=='svc' else 'mixed_asset_id']
            return FileResponse(assets / asset_id,filename=kind+'.wav',media_type='audio/wav')
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

    @app.get('/api/assets/{asset_id}/waveform')
    def asset_waveform(asset_id: str):
        import re
        if not re.fullmatch(r'[a-f0-9]{32}\.(wav|mp3|flac|m4a|ogg|aac|opus|webm)',asset_id):raise FileNotFoundError()
        path=assets/asset_id
        if not path.is_file():raise FileNotFoundError()
        return {'peaks':waveform(path),'sample_interval':.005}

    app.mount('/', StaticFiles(directory=ROOT / 'design', html=True), name='ui')
    return app


if __name__ == '__main__':
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=18733)
    args = parser.parse_args()
    uvicorn.run(create_app(), host='127.0.0.1', port=args.port)
