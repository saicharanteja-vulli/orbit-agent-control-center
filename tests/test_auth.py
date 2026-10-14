import json
import time
import pytest
from fastapi.testclient import TestClient
from backend.app import app
from backend.db import connect
from backend.worker import claim,process
from backend.agent_runtime import propose,dispatch_action

PASSWORD='long-test-password-2026'

@pytest.fixture
def clients(tmp_path,monkeypatch):
    monkeypatch.setenv('CONTROL_DB',str(tmp_path/'auth.db'))
    with TestClient(app) as admin, TestClient(app) as member, TestClient(app) as other:
        for c in (admin,member,other):c.headers['X-Orbit-Request']='1'
        r=admin.post('/api/auth/setup',json={'username':'admin','password':PASSWORD})
        assert r.status_code==200
        admin.headers['X-Orbit-CSRF']=r.json()['csrf']
        for name,c in [('member',member),('other',other)]:
            assert admin.post('/api/auth/users',json={'username':name,'password':PASSWORD,'role':'member'}).status_code==201
            r=c.post('/api/auth/login',json={'username':name,'password':PASSWORD});assert r.status_code==200
            c.headers['X-Orbit-CSRF']=r.json()['csrf']
        yield admin,member,other


def test_sessions_and_csrf(clients):
    admin,member,_=clients
    r=member.get('/api/auth/me');uid=r.json()['user']['id']
    with connect() as db:
        row=db.execute('SELECT password_hash FROM users WHERE id=?',(uid,)).fetchone()
        assert PASSWORD not in row[0]
        assert member.cookies.get('orbit_session') not in [r[0] for r in db.execute('SELECT token_hash FROM sessions')]
    assert member.post('/api/runs',json={'task':'blocked'},headers={'X-Orbit-CSRF':'wrong'}).status_code==403
    assert member.post('/api/runs',json={'task':'blocked'},headers={'Origin':'https://evil.example'}).status_code==403
    assert member.post('/api/runs',json={'task':'blocked'},headers={'X-Orbit-Request':''}).status_code==403
    assert member.post('/api/auth/logout').status_code==200
    assert member.get('/api/runs').status_code==401
    assert member.get('/api/auth/users').status_code==401
    assert admin.post('/api/auth/setup',json={'username':'hijack','password':PASSWORD}).status_code==409


def test_ownership_all_paths_even_admin(clients):
    admin,owner,other=clients
    run=owner.post('/api/runs',json={'task':'Private owner task','approval_required':False}).json()
    claim()
    aid=propose(run['id'],'save_file',{'filename':'private.md','content':'owner only'},{'messages':[]},0)
    action=owner.get('/api/runs/'+run['id']).json()['actions'][0]
    for outsider in (admin,other):
        assert outsider.get('/api/runs').json()==[]
        for suffix in ('','/export','/stream'):
            assert outsider.get('/api/runs/'+run['id']+suffix).status_code==404
        for suffix in ('/approve','/reject','/retry','/cancel'):
            assert outsider.post('/api/runs/'+run['id']+suffix).status_code==404
        assert outsider.post('/api/actions/'+aid+'/decision',json={'approved':True,'digest':action['digest']}).status_code==404
        assert outsider.get('/api/actions/'+aid+'/artifact').status_code==404
    assert owner.post('/api/actions/'+aid+'/decision',json={'approved':True,'digest':action['digest']}).status_code==200
    claim();dispatch_action(aid)
    assert owner.get('/api/actions/'+aid+'/artifact').text=='owner only'
    assert other.get('/api/actions/'+aid+'/artifact').status_code==404
    assert admin.get('/api/actions/'+aid+'/artifact').status_code==404


def test_roles_and_revocation(clients):
    admin,member,_=clients
    uid=member.get('/api/auth/me').json()['user']['id']
    for path,method,body in [('/api/queue/pause','post',{}),('/api/agents/planner','patch',{'enabled':False}),('/api/integrations/webhook','put',{'url':''}),('/api/auth/users','post',{'username':'evil','password':PASSWORD,'role':'admin'})]:
        assert getattr(member,method)(path,json=body).status_code==403
    assert member.get('/api/auth/users').status_code==403
    assert member.post('/api/runs',json={'task':'send webhook','enabled_tools':['send_webhook']}).status_code==403
    assert admin.patch('/api/auth/users/'+uid,json={'role':'viewer','active':True}).status_code==200
    assert member.get('/api/runs').status_code==401
    r=member.post('/api/auth/login',json={'username':'member','password':PASSWORD})
    member.headers['X-Orbit-CSRF']=r.json()['csrf']
    assert member.get('/api/runs').status_code==200
    assert member.post('/api/runs',json={'task':'viewer task'}).status_code==403
    assert admin.patch('/api/auth/users/'+uid,json={'role':'member','active':False}).status_code==200
    assert member.get('/api/runs').status_code==401
    assert member.post('/api/auth/login',json={'username':'member','password':PASSWORD}).status_code==401


def test_expiry_and_rate_limit(clients):
    _,member,_=clients
    uid=member.get('/api/auth/me').json()['user']['id']
    with connect() as db:db.execute('UPDATE sessions SET expires=? WHERE user_id=?',(time.time()-1,uid))
    assert member.get('/api/runs').status_code==401
    for _ in range(5):
        assert member.post('/api/auth/login',json={'username':'member','password':'incorrect-password'}).status_code==401
    assert member.post('/api/auth/login',json={'username':'member','password':PASSWORD}).status_code==429


def test_cookie_flags_and_legacy_migration(tmp_path,monkeypatch):
    monkeypatch.setenv('CONTROL_DB',str(tmp_path/'migration.db'))
    with TestClient(app) as c:
        with connect() as db:
            db.execute("INSERT INTO runs(id,task,status,created_at,updated_at) VALUES('legacy','old task','completed','2026','2026')")
        assert c.get('/api/runs').status_code==401
        c.headers['X-Orbit-Request']='1'
        r=c.post('/api/auth/setup',json={'username':'owner','password':PASSWORD})
        assert 'HttpOnly' in r.headers['set-cookie'] and 'SameSite=strict' in r.headers['set-cookie']
        c.headers['X-Orbit-CSRF']=r.json()['csrf']
        assert c.get('/api/runs').json()[0]['owner_id']==r.json()['user']['id']
        assert c.get('/api/runs').headers['cache-control']=='no-store'
        assert c.post('/api/auth/users',json={'username':'owner','password':PASSWORD}).status_code==409


def test_viewer_cannot_approve_own_pending_action(clients):
    admin,member,_=clients
    uid=member.get('/api/auth/me').json()['user']['id']
    run=member.post('/api/runs',json={'task':'My pending file'}).json()
    claim()
    aid=propose(run['id'],'save_file',{'filename':'review.md','content':'private draft'},{'messages':[]},0)
    action=member.get('/api/runs/'+run['id']).json()['actions'][0]
    admin.patch('/api/auth/users/'+uid,json={'role':'viewer','active':True})
    response=member.post('/api/auth/login',json={'username':'member','password':PASSWORD})
    member.headers['X-Orbit-CSRF']=response.json()['csrf']
    assert member.get('/api/runs/'+run['id']).status_code==200
    assert member.post('/api/actions/'+aid+'/decision',json={'approved':True,'digest':action['digest']}).status_code==403
    assert member.get('/api/runs/'+run['id']).json()['actions'][0]['status']=='pending'
