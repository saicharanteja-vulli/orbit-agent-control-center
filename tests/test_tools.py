import json
import socket
import sqlite3
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from backend.app import app
from backend.db import connect
from backend.worker import claim, process, recover
from backend import tools
from backend.agent_runtime import propose, dispatch_action


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('CONTROL_DB', str(tmp_path / 'test.db'))
    with TestClient(app) as c:
        c.headers['X-Orbit-Request']='1'
        auth=c.post('/api/auth/setup',json={'username':'testadmin','password':'test-password-12345'}).json()
        c.headers['X-Orbit-CSRF']=auth['csrf']
        yield c


def make_action(client, name='save_file', args=None):
    run = client.post('/api/runs', json={'task': 'Save a test file', 'approval_required': False}).json()
    claim()
    state = {'messages': [], 'steps': 1, 'pending_action': None}
    action_id = propose(run['id'], name, args or {'filename': 'test.md', 'content': '# Approved content'}, state, 12)
    action = client.get('/api/runs/' + run['id']).json()['actions'][0]
    return run, action


def test_write_requires_exact_approval_and_only_executes_once(client):
    run, action = make_action(client)
    assert not tools.artifact_root().exists()
    assert dispatch_action(action['id']) is None  # run is paused, not running
    assert client.post('/api/runs/' + run['id'] + '/approve').status_code == 409
    route = '/api/actions/' + action['id'] + '/decision'
    assert client.post(route, json={'approved': True, 'digest': '0'*64}).status_code == 409
    approved = client.post(route, json={'approved': True, 'digest': action['digest']})
    assert approved.status_code == 200
    assert approved.json()['status'] == 'queued'
    assert not tools.artifact_root().exists()
    assert client.post(route, json={'approved': True, 'digest': action['digest']}).status_code == 409
    claim()
    result = dispatch_action(action['id'])
    assert result['saved'] is True
    with pytest.raises(ValueError):
        dispatch_action(action['id'])
    assert client.get(result['download_url']).text == '# Approved content'


def test_report_export_setting_cannot_bypass_tool_approval(client):
    run, action = make_action(client)
    assert run['approval_required'] == 0
    assert action['requires_approval'] == 1
    assert action['status'] == 'pending'


def test_rejection_and_cancel_do_not_write(client):
    run, action = make_action(client)
    client.post('/api/actions/' + action['id'] + '/decision', json={'approved': False, 'digest': action['digest']})
    claim()
    assert 'rejected' in dispatch_action(action['id'])['error']
    assert not tools.artifact_root().exists()
    client.post('/api/runs/' + run['id'] + '/cancel')
    run, action = make_action(client)
    client.post('/api/actions/' + action['id'] + '/decision', json={'approved': True, 'digest': action['digest']})
    client.post('/api/runs/' + run['id'] + '/cancel')
    assert dispatch_action(action['id']) is None
    assert not tools.artifact_root().exists()


def test_proposal_arguments_immutable(client):
    _, action = make_action(client)
    with pytest.raises(sqlite3.IntegrityError):
        with connect() as db:
            db.execute('UPDATE actions SET arguments=? WHERE id=?', ('{}', action['id']))


def test_pending_approval_survives_worker_restart(client):
    run, action = make_action(client)
    recover()
    saved = client.get('/api/runs/' + run['id']).json()
    assert saved['status'] == 'awaiting_approval'
    assert saved['actions'][0]['status'] == 'pending'


def test_interrupted_dispatch_is_unknown_never_replayed(client):
    run, action = make_action(client)
    client.post('/api/actions/' + action['id'] + '/decision', json={'approved': True, 'digest': action['digest']})
    claim()
    with connect() as db:
        db.execute("UPDATE actions SET status='executing' WHERE id=?", (action['id'],))
    recover()
    saved = client.get('/api/runs/' + run['id']).json()
    assert saved['status'] == 'failed'
    assert saved['actions'][0]['status'] == 'unknown'
    assert dispatch_action(action['id']) is None


@pytest.mark.parametrize('filename', ['../secret.txt', '/tmp/file.md', 'script.py', '.env', 'nested/file.md', 'x.html'])
def test_file_path_allowlist(filename):
    with pytest.raises(ValueError):
        tools.validate('save_file', {'filename': filename, 'content': 'x'})


@pytest.mark.parametrize('url', ['http://example.com', 'https://user:pass@example.com', 'https://example.com:8000'])
def test_bad_url_schemes_and_credentials(url):
    with pytest.raises(ValueError):
        tools.public_target(url)


@pytest.mark.parametrize('ip', ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', '192.168.1.1', '0.0.0.0'])
def test_private_dns_blocked(monkeypatch, ip):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 443))])
    with pytest.raises(ValueError):
        tools.public_target('https://example.com/path')


def test_read_tool_executes_without_write_approval(client, monkeypatch):
    monkeypatch.setattr(tools, 'web_search', lambda q: {'results': [{'url': 'https://example.com', 'title': q}]})
    run, action = make_action(client, 'web_search', {'query': 'test search'})
    assert action['status'] == 'ready'
    assert action['requires_approval'] == 0
    assert dispatch_action(action['id'])['results'][0]['title'] == 'test search'


def test_agent_loop_resume_after_approval(client, monkeypatch):
    from backend import agent_runtime
    run = client.post('/api/runs', json={'task': 'Save a test file', 'approval_required': False}).json()
    with connect() as db:
        db.execute("UPDATE runs SET mode='ollama',enabled_tools=? WHERE id=?", (json.dumps(['save_file']), run['id']))
    calls = iter([
        ({'role': 'assistant', 'content': '', 'tool_calls': [{'function': {'name': 'save_file', 'arguments': {'filename': 'result.txt', 'content': 'Reviewed text'}}}]}, 10),
        ({'role': 'assistant', 'content': 'The approved file was saved.'}, 5),
    ])
    monkeypatch.setattr(agent_runtime, 'model_turn', lambda *a: next(calls))
    process(claim(), delay=0)
    saved = client.get('/api/runs/' + run['id']).json()
    assert saved['status'] == 'awaiting_approval'
    action = saved['actions'][0]
    client.post('/api/actions/' + action['id'] + '/decision', json={'approved': True, 'digest': action['digest']})
    process(claim(), delay=0)
    saved = client.get('/api/runs/' + run['id']).json()
    assert saved['status'] == 'completed'
    assert saved['tokens'] == 15
    assert saved['actions'][0]['status'] == 'succeeded'
    assert client.get('/api/actions/' + action['id'] + '/artifact').text == 'Reviewed text'


def test_demo_cannot_execute_tools(client):
    response = client.post('/api/runs', json={'task': 'test', 'enabled_tools': ['save_file']})
    assert response.status_code == 400


def test_webhook_approved_destination_frozen_and_never_leaks_to_model(client, monkeypatch):
    monkeypatch.setattr(tools, 'public_target', lambda url: (None, 'hooks.example.com', '1.1.1.1'))
    assert client.put('/api/integrations/webhook', json={'url': 'https://hooks.example.com/secret'}).status_code == 200
    config = client.get('/api/integrations/webhook').json()
    assert config == {'configured': True, 'host': 'hooks.example.com'}
    run, action = make_action(client, 'send_webhook', {'payload': {'message': 'synthetic test'}})
    assert json.loads(action['arguments'])['destination'] == 'https://hooks.example.com/secret'
    sends = []
    monkeypatch.setattr(tools, 'post_webhook', lambda dest, payload, aid: sends.append((dest,payload,aid)) or {'accepted': True})
    assert dispatch_action(action['id']) is None
    assert sends == []
    client.post('/api/actions/'+action['id']+'/decision', json={'approved': True, 'digest': action['digest']})
    claim()
    assert dispatch_action(action['id']) == {'accepted': True}
    assert len(sends) == 1
    with pytest.raises(ValueError):
        dispatch_action(action['id'])
    assert len(sends) == 1


def test_disconnect_invalidates_old_approvals(client, monkeypatch):
    monkeypatch.setattr(tools, 'public_target', lambda url: (None, 'hooks.example.com', '1.1.1.1'))
    client.put('/api/integrations/webhook', json={'url': 'https://hooks.example.com/old'})
    run, action = make_action(client, 'send_webhook', {'payload': {'test': True}})
    client.post('/api/actions/'+action['id']+'/decision', json={'approved': True, 'digest': action['digest']})
    client.put('/api/integrations/webhook', json={'url': ''})
    claim()
    assert 'rejected' in dispatch_action(action['id'])['error']


def test_webhook_timeout_unknown_no_automatic_retry(client, monkeypatch):
    with connect() as db:
        db.execute("INSERT INTO settings VALUES('webhook_url','https://hooks.example.com/test')")
    run, action = make_action(client, 'send_webhook', {'payload': {'test': True}})
    client.post('/api/actions/'+action['id']+'/decision', json={'approved': True, 'digest': action['digest']})
    claim()
    def timeout(*args): raise TimeoutError('Response timed out')
    monkeypatch.setattr(tools, 'post_webhook', timeout)
    assert dispatch_action(action['id'])['outcome'] == 'unknown'
    with pytest.raises(ValueError):
        dispatch_action(action['id'])


def test_webhook_no_redirect_and_one_send(monkeypatch):
    from urllib.parse import urlsplit
    monkeypatch.setattr(tools, 'public_target', lambda url: (urlsplit(url), 'hooks.example.com', '1.1.1.1'))
    requests=[]
    class Response:
        status=302
        def read(self,n): return b'redirect'
    class Connection:
        def __init__(self,*args): pass
        def request(self,*args,**kwargs): requests.append((args,kwargs))
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr(tools, 'PinnedHTTPS', Connection)
    with pytest.raises(RuntimeError):
        tools.post_webhook('https://hooks.example.com/test', {'hello':'world'}, 'action-id')
    assert len(requests)==1
    assert requests[0][1]['headers']['Idempotency-Key']=='action-id'


def test_read_redirect_to_private_network_blocked(monkeypatch):
    from urllib.parse import urlsplit
    connections=[]
    def target(url):
        if '127.0.0.1' in url: raise ValueError('private')
        return urlsplit(url), 'example.com', '1.1.1.1'
    monkeypatch.setattr(tools, 'public_target', target)
    class Response:
        status=302
        def getheader(self,name): return 'https://127.0.0.1/secret'
    class Connection:
        def __init__(self,*args): connections.append(args)
        def request(self,*args,**kwargs): pass
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr(tools, 'PinnedHTTPS', Connection)
    with pytest.raises(ValueError): tools.fetch_public('https://example.com')
    assert len(connections)==1


def test_disabled_tool_cannot_be_executed_by_model(client, monkeypatch):
    from backend import agent_runtime
    run=client.post('/api/runs',json={'task':'Try a tool','approval_required':False}).json()
    with connect() as db:
        db.execute("UPDATE runs SET mode='ollama',enabled_tools=? WHERE id=?", (json.dumps(['web_search']),run['id']))
    turns=iter([
        ({'role':'assistant','content':'','tool_calls':[{'function':{'name':'save_file','arguments':{'filename':'blocked.txt','content':'no'}}}]},3),
        ({'role':'assistant','content':'File creation is not enabled.'},2),
    ])
    monkeypatch.setattr(agent_runtime,'model_turn',lambda *args:next(turns))
    process(claim(),delay=0)
    saved=client.get('/api/runs/'+run['id']).json()
    assert saved['status']=='completed'
    assert saved['actions']==[]
    assert not tools.artifact_root().exists()


def test_webhook_destination_cannot_be_set_by_model():
    with pytest.raises(ValueError):
        tools.validate('send_webhook',{'payload':{},'destination':'https://evil.example'})


def test_structured_protocol_still_enforces_allowlist():
    from backend.agent_runtime import parse_decision
    decision=parse_decision('{"tool":"save_file","arguments":{"filename":"test.md","content":"ok"}}',['save_file'])
    assert decision['tool_calls'][0]['function']['name']=='save_file'
    with pytest.raises(ValueError):
        parse_decision('{"tool":"send_webhook","arguments":{"payload":{}}}',['save_file'])
    with pytest.raises(ValueError):
        parse_decision('{"tool":"save_file","arguments":{"filename":"../bad.txt","content":"bad"}}',['save_file'])


def test_sourced_report_full_delivery(client, monkeypatch):
    from backend import agent_runtime
    run = client.post('/api/runs', json={'task': 'Research and save a report', 'approval_required': False}).json()
    with connect() as db:
        db.execute("UPDATE runs SET mode='ollama',enabled_tools=? WHERE id=?", (json.dumps(['web_search','read_webpage','save_file']), run['id']))
    def call(name, args):
        return ({'role':'assistant','content':'','tool_calls':[{'function':{'name':name,'arguments':args}}]}, 10)
    turns = iter([call('web_search', {'query':'official example'}), call('read_webpage', {'url':'https://example.com/source'}), call('save_file', {'filename':'report.md','content':'# Report\\n\\nA sourced draft.'})])
    monkeypatch.setattr(agent_runtime, 'model_turn', lambda *a: next(turns))
    monkeypatch.setattr(tools, 'web_search', lambda q: {'results':[{'url':'https://example.com/source'}]})
    monkeypatch.setattr(tools, 'fetch_public', lambda url: {'url':url,'text':'Supporting source text'})
    process(claim(), delay=0)
    detail = client.get('/api/runs/'+run['id']).json()
    assert detail['status'] == 'awaiting_approval'
    action = detail['actions'][-1]
    reviewed = json.loads(action['arguments'])['content']
    assert '\n\nA sourced draft.' in reviewed
    assert '[Source 1](https://example.com/source)' in reviewed
    assert not tools.artifact_root().exists()
    assert client.post('/api/actions/'+action['id']+'/decision', json={'approved':True,'digest':action['digest']}).status_code == 200
    process(claim(), delay=0)
    detail = client.get('/api/runs/'+run['id']).json()
    assert detail['status'] == 'completed'
    assert json.loads(detail['result'])['output'] == reviewed
    assert client.get('/api/actions/'+action['id']+'/artifact').text == reviewed
    assert detail['actions'][-1]['status'] == 'succeeded'


def test_research_requires_read_evidence(client):
    run = client.post('/api/runs', json={'task':'Research then save'}).json()
    with connect() as db:
        db.execute('UPDATE runs SET enabled_tools=? WHERE id=?', (json.dumps(['web_search','save_file']),run['id']))
    claim()
    with pytest.raises(ValueError, match='successfully read'):
        propose(run['id'],'save_file',{'filename':'r.md','content':'Unverified draft'}, {'messages':[]},0)
    assert not tools.artifact_root().exists()


def test_research_blocks_invented_links(client, monkeypatch):
    run, action = make_action(client, 'web_search', {'query':'source'})
    monkeypatch.setattr(tools, 'web_search', lambda q: {'results':[]})
    dispatch_action(action['id'])
    state={'messages':[]}
    aid=propose(run['id'],'read_webpage',{'url':'https://example.com/source'},state,0)
    monkeypatch.setattr(tools,'fetch_public',lambda url:{'url':url,'text':'evidence'})
    dispatch_action(aid)
    with pytest.raises(ValueError, match='not supported'):
        propose(run['id'],'save_file',{'filename':'r.md','content':'See https://invented.example/fake'},state,0)
    assert not tools.artifact_root().exists()
