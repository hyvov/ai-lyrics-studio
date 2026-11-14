"""Exercise an isolated installed API with public upstream demo audio."""
import argparse
import json
from pathlib import Path
import subprocess
import time
import urllib.request
import wave


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:19001')
    parser.add_argument('--installed', type=Path, required=True)
    parser.add_argument('--examples', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--tasks', nargs='+', default=['render','svc','separation'])
    args=parser.parse_args();args.output.mkdir(parents=True, exist_ok=True)
    base=args.url+'/api/';auth={}
    def api(path,data=None,method=None,headers=None):
        hdr={**auth,**(headers or {})}
        if isinstance(data,dict):data=json.dumps(data).encode();hdr['Content-Type']='application/json'
        with urllib.request.urlopen(urllib.request.Request(base+path,data=data,method=method,headers=hdr),timeout=30) as response:
            payload=response.read()
            return json.loads(payload) if 'json' in response.headers.get('Content-Type','') else payload
    session=api('session');assert Path(session['installation'])==args.installed.resolve()
    auth['x-studio-token']=session['token']
    results={'installation':session['installation'],'tasks':{}}
    ffmpeg=args.installed/'runtime/independent/python/ffmpeg/bin/ffmpeg.exe'
    def audio(name,seconds):
        dest=args.output/(name+'.wav')
        subprocess.run([str(ffmpeg),'-nostdin','-v','error','-y','-i',str(args.examples/(name+'.mp3')),'-t',str(seconds),'-ar','24000','-ac','1',str(dest)],check=True)
        with wave.open(str(dest)) as wav: duration=wav.getnframes()/wav.getframerate()
        return dest,duration
    def attach(p,kind,media):
        path,duration=media
        return api(f'projects/{p["id"]}/assets/{kind}',path.read_bytes(),headers={
            'x-project-revision':str(p['revision']),'x-file-name':path.name,'x-audio-duration':str(duration)})
    def wait(job):
        prior=None;deadline=time.monotonic()+900
        while time.monotonic()<deadline:
            job=api('jobs/'+job['id'])
            stamp=(job['status'],job['stage'])
            if stamp!=prior:print(job['kind'],stamp,job.get('error'),flush=True);prior=stamp
            if job['status'] not in ('running','queued'):
                return job
            time.sleep(2)
        raise TimeoutError('Inference exceeded 15 minutes')
    voice=audio('zh_target',6.71)
    for task in args.tasks:
        p=api('projects',{'name':'安装验收 · '+task,'duration':voice[1],'lines':[]})
        if task=='separation':
            p=attach(p,'song',audio('music',8))
            job=api(f'projects/{p["id"]}/separation',{'revision':p['revision']})
        else:
            p=attach(p,'source',voice)
            if task=='render':
                p=api(f'projects/{p["id"]}/lyrics',{'revision':p['revision'],'text':'[00:00.00]像我这样懦弱的人凡事都要留几分'})
                p['lines'][0].update(text='像我这样脆弱的人凡事都要留几分',mode='score',generation_params={'steps':32,'cfg':3,'seed':42})
                p=api('projects/'+p['id'],p,'PUT')
                job=api(f'projects/{p["id"]}/renders',{'revision':p['revision'],'segment_ids':[p['lines'][0]['id']]})
            elif task=='svc':
                p=attach(p,'reference_pending',audio('svc_prompt_demo',4))
                job=api(f'projects/{p["id"]}/svc',{'revision':p['revision'],'target_kind':'source','options':{'quality':'fast','auto_shift':False}})
            elif task=='transcription':
                job=api(f'projects/{p["id"]}/transcription',{'revision':p['revision'],'replace':False})
            else:raise ValueError(task)
        result=wait(job);results['tasks'][task]=result
        (args.output/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf8')
    if any(x['status']!='succeeded' for x in results['tasks'].values()):raise SystemExit(1)


if __name__=='__main__':main()
