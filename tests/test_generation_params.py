import copy
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.generation_params import generation_params
from backend.singing import fingerprint


class GenerationParameterTests(unittest.TestCase):
    def test_defaults_and_invalid_values(self):
        self.assertEqual(generation_params('ying')['steps'],64)
        self.assertEqual(generation_params('score')['seed'],-1)
        for value in ({'steps':3},{'steps':4.5},{'cfg':float('nan')},{'seed':True},{'t_shift':2}):
            with self.assertRaises(ValueError):generation_params('ying',value)
        with self.assertRaises(ValueError):generation_params('score',{'t_shift':.5})

    def test_defaults_keep_old_fingerprint_but_changes_invalidate_only_that_line(self):
        line={'id':'a','original':'你好','text':'你早','start':0.,'end':2.,'mode':'score','restore':False}
        other={'id':'b','original':'晚风','text':'晨风','start':2.,'end':4.,'mode':'score','restore':False}
        p={'assets':{'source':{'id':'source.wav'}},'lines':[line,other]}
        old=fingerprint(p,line);before=fingerprint(p,other)
        line['generation_params']=generation_params('score');self.assertEqual(old,fingerprint(p,line))
        line['generation_params']['cfg']=4.;self.assertNotEqual(old,fingerprint(p,line));self.assertEqual(before,fingerprint(p,other))

    def test_phrase_api_saves_parameters_and_rejects_invalid_without_writing(self):
        with tempfile.TemporaryDirectory() as temp, TestClient(create_app(Path(temp)),base_url='http://127.0.0.1:18733') as c:
            auth={'x-studio-token':c.get('/api/session').json()['token']}
            line={'id':'a','original':'你好','text':'你早','start':0.,'end':2.,'mode':'ying','restore':False}
            p=c.post('/api/projects',headers=auth,json={'name':'test','duration':2,'lines':[line]}).json()
            url=f"/api/projects/{p['id']}/segments/a/phrase"
            value={'revision':p['revision'],'text':'你早','start':0.,'end':2.,'mode':'ying','restore':False,
                   'generation_params':{'steps':16,'cfg':1.5,'seed':123,'t_shift':.3,'silence':.2}}
            result=c.put(url,headers=auth,json=value);self.assertEqual(result.status_code,200,result.text)
            p=result.json();self.assertEqual(p['lines'][0]['generation_params']['steps'],16)
            value['revision']=p['revision'];value['generation_params']['steps']=999
            self.assertEqual(c.put(url,headers=auth,json=value).status_code,400)
            after=c.get(f"/api/projects/{p['id']}").json();self.assertEqual(after['revision'],p['revision']);self.assertEqual(after['lines'][0]['generation_params']['steps'],16)


if __name__=='__main__': unittest.main()
