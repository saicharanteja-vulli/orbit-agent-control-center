import pytest
from fastapi.testclient import TestClient
from backend.app import app
from backend.db import connect,init
from backend.auth import password_hash
from backend.deployment import validate_config

PASSWORD='production-test-password'

@pytest.fixture
def production_client(tmp_path,monkeypatch):
    monkeypatch.setenv('CONTROL_DB',str(tmp_path/'prod.db'))
    monkeypatch.setenv('ORBIT_ENV','production')
    monkeypatch.setenv('ORBIT_PUBLIC_URL','https://orbit.example.org')
    monkeypatch.setenv('ORBIT_SECURE_COOKIE','1')
    monkeypatch.setenv('ORBIT_SIGNUP_ENABLED','1')
    init()
    with connect() as db:db.execute('INSERT INTO users VALUES(?,?,?,?,1)',('admin','admin',password_hash(PASSWORD),'admin'))
    with TestClient(app,base_url='https://orbit.example.org') as c:
        c.headers['X-Orbit-Request']='1'
        c.headers['X-Orbit-Client-IP']='198.51.100.1'
        yield c


def signup(c,name='member'):
    r=c.post('/api/auth/signup',json={'username':name,'password':PASSWORD,'role':'admin'})
    assert r.status_code==201,r.text
    c.headers['X-Orbit-CSRF']=r.json()['csrf']
    return r


def test_production_signup_no_escalation(production_client):
    c=production_client
    assert c.get('/api/auth/status').json()=={'setup_required':False,'signup_enabled':True}
    assert c.post('/api/auth/setup',json={'username':'hijack','password':PASSWORD}).status_code==403
    r=signup(c)
    assert r.json()['user']['role']=='member'
    assert 'Secure' in r.headers['set-cookie']
    assert c.get('/api/auth/users').status_code==403
    assert c.get('/api/runs').json()==[]
    assert c.post('/api/runs',json={'task':'my public task'}).status_code==201


def test_host_origin_csp_and_body_limits(production_client):
    c=production_client
    assert c.get('/api/auth/status',headers={'Host':'evil.example'}).status_code==403
    assert c.post('/api/auth/signup',json={'username':'member','password':PASSWORD},headers={'Origin':'http://orbit.example.org'}).status_code==403
    assert c.post('/api/auth/signup',json={'username':'member','password':PASSWORD},headers={'Sec-Fetch-Site':'cross-site'}).status_code==403
    assert c.post('/api/auth/login',content='x'*65537).status_code==413
    headers=c.get('/api/auth/status').headers
    assert "frame-ancestors 'none'" in headers['content-security-policy']
    assert headers['strict-transport-security']=='max-age=31536000'
    assert c.get('/openapi.json').status_code==404


def test_signup_capacity_and_closed(production_client,monkeypatch):
    c=production_client
    monkeypatch.setenv('ORBIT_MAX_USERS','1')
    assert c.post('/api/auth/signup',json={'username':'member','password':PASSWORD}).status_code==429
    monkeypatch.setenv('ORBIT_SIGNUP_ENABLED','0')
    assert c.post('/api/auth/signup',json={'username':'member','password':PASSWORD}).status_code==403


def test_signup_rate_limit(production_client):
    c=production_client
    for i in range(3):signup(c,'member'+str(i))
    assert c.post('/api/auth/signup',json={'username':'member4','password':PASSWORD}).status_code==429


def test_pending_task_quota(production_client):
    c=production_client;signup(c)
    for _ in range(3):assert c.post('/api/runs',json={'task':'My queued task'}).status_code==201
    assert c.post('/api/runs',json={'task':'Overflow task'}).status_code==429


def test_password_change_revokes_sessions(production_client):
    c=production_client;signup(c)
    old=c.cookies.get('orbit_session')
    r=c.post('/api/auth/password',json={'current_password':PASSWORD,'new_password':'another-long-password'})
    assert r.status_code==200
    assert c.get('/api/auth/me').status_code==200
    with TestClient(app,base_url='https://orbit.example.org') as stale:
        stale.cookies.set('orbit_session',old)
        assert stale.get('/api/runs').status_code==401


def test_config_fails_closed(monkeypatch):
    monkeypatch.setenv('ORBIT_ENV','production')
    monkeypatch.setenv('ORBIT_PUBLIC_URL','http://orbit.example.org')
    with pytest.raises(RuntimeError):validate_config()
    monkeypatch.setenv('ORBIT_PUBLIC_URL','https://orbit.example.org')
    monkeypatch.setenv('ORBIT_SECURE_COOKIE','0')
    with pytest.raises(RuntimeError):validate_config()


def test_backup_restore_roundtrip(tmp_path,monkeypatch):
    import subprocess,sys,os
    from pathlib import Path
    source=tmp_path/'source'/'control.db'
    monkeypatch.setenv('CONTROL_DB',str(source));init()
    with connect() as db:
        db.execute('INSERT INTO users VALUES(?,?,?,?,1)',('u','backupuser',password_hash(PASSWORD),'member'))
        db.execute('INSERT INTO sessions VALUES(?,?,?,?)',('hash','u','csrf',9999999999))
    artifact=source.parent/'artifacts'/'run'/'action'/'report.md'
    artifact.parent.mkdir(parents=True);artifact.write_text('# Report\nSource evidence')
    archive=subprocess.run([sys.executable,'-m','backend.backup','backup'],capture_output=True,check=True).stdout
    destination=tmp_path/'restored'/'control.db'
    env={**os.environ,'CONTROL_DB':str(destination)}
    subprocess.run([sys.executable,'-m','backend.backup','restore'],input=archive,env=env,capture_output=True,check=True)
    import sqlite3
    with sqlite3.connect(destination) as db:
        assert db.execute('SELECT username FROM users').fetchone()[0]=='backupuser'
        assert db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0
    assert (destination.parent/'artifacts/run/action/report.md').read_text()==artifact.read_text()
    assert subprocess.run([sys.executable,'-m','backend.backup','restore'],input=archive,env=env,capture_output=True).returncode!=0


def test_backup_restore_rejects_traversal(tmp_path,monkeypatch):
    import io,tarfile,subprocess,sys,os
    payload=io.BytesIO()
    with tarfile.open(fileobj=payload,mode='w:gz') as archive:
        entry=tarfile.TarInfo('../escaped');entry.size=3;archive.addfile(entry,io.BytesIO(b'bad'))
    env={**os.environ,'CONTROL_DB':str(tmp_path/'data/control.db')}
    r=subprocess.run([sys.executable,'-m','backend.backup','restore'],input=payload.getvalue(),env=env,capture_output=True)
    assert r.returncode!=0
    assert not (tmp_path/'escaped').exists()


def test_quick_tunnel_cannot_spoof_rate_limit_bucket(production_client, monkeypatch):
    from backend.auth import client_ip
    from starlette.requests import Request
    monkeypatch.setenv('ORBIT_QUICK_TUNNEL','1')
    for ip in ('198.51.100.1','198.51.100.2'):
        request=Request({'type':'http','headers':[(b'x-orbit-client-ip',ip.encode()),(b'cf-connecting-ip',ip.encode())]})
        assert client_ip(request)=='quick-tunnel'
