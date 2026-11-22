
import os
import tempfile
from pathlib import Path

# Always use an isolated temporary database. Never touch the live database.
_tmp = tempfile.TemporaryDirectory(prefix='kendo_test_')
os.environ['DATABASE_URL'] = 'sqlite:///' + str(Path(_tmp.name) / 'test.db')
os.environ['SECRET_KEY'] = 'local-test-only'

from datetime import date
from fastapi.testclient import TestClient
from sqlalchemy import select
import main, mobile_api

db=main.SessionLocal()
u=main.User(username="testuser",password_hash=main.hash_password("testpass"),role="member")
db.add(u);db.commit()
client=TestClient(main.app)
resp=client.post('/api/v1/auth/login',json={'username':'testuser','password':'testpass'})
assert resp.status_code==200, resp.text
headers={'Authorization':'Bearer '+resp.json()['access_token']}
def req(method,url,**kwargs):
    r=client.request(method,url,headers=headers,**kwargs)
    assert r.status_code < 400, (method,url,r.status_code,r.text)
    return r
t=req('POST','/api/v1/tournaments',json={'name':'テスト大会','event_date':'2026-11-19','venue':'東京武道館'}).json()
tid=t['id']
assert main.tournament_view(db.get(main.Tournament,tid), db)['individual_sections'] == []
req('PUT',f'/api/v1/tournaments/{tid}',json={'name':'テスト大会更新','event_date':'2026-11-19'})
assert db.get(main.Tournament,tid).venue=='東京武道館'
req('PUT',f'/api/v1/tournaments/{tid}/individual-awards',json={'player_name':'選手A','award':'優勝'})
req('PUT',f'/api/v1/tournaments/{tid}/team-award',json={'award':'準優勝'})
entry={'player_name':'選手A','result':'win','score_for':2,'score_against':0}
payload={'event_type':'individual','tournament_id':tid,'entries':[entry],'tournament':'テスト大会更新','event_date':'2026-11-19'}
eid=req('POST','/api/v1/events',json=payload).json()['id']
payload['entries']=[{**entry,'result':'loss','score_for':0,'score_against':1}]
req('PUT',f'/api/v1/events/{eid}',json=payload)
assert req('GET',f'/api/v1/events/{eid}').json()['entries'][0]['result']=='loss'
bad={**payload,'entries':[{**entry,'score_for':-1}]}
assert client.put(f'/api/v1/events/{eid}',headers=headers,json=bad).status_code==422
badteam={'event_type':'team','tournament_id':tid,'entries':[entry]}
assert client.post('/api/v1/events',headers=headers,json=badteam).status_code==422
req('DELETE',f'/api/v1/tournaments/{tid}')
assert db.get(main.Tournament,tid) is None
assert db.scalars(select(mobile_api.IndividualAward).where(mobile_api.IndividualAward.tournament_id==tid)).all()==[]
assert db.get(mobile_api.TeamAward,tid) is None
print('PASS: empty tournament detail, login, tournament update preserving venue, individual create/edit, validation, tournament cascade/awards cleanup')

db.close()
main.engine.dispose()
_tmp.cleanup()
