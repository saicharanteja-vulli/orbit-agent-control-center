import json
import pytest
from fastapi.testclient import TestClient
from backend.app import app
from backend.db import connect
from backend.worker import claim, process, recover


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('CONTROL_DB', str(tmp_path / 'test.db'))
    with TestClient(app) as c:
        c.headers['X-Orbit-Request']='1'
        auth=c.post('/api/auth/setup',json={'username':'testadmin','password':'test-password-12345'}).json()
        c.headers['X-Orbit-CSRF']=auth['csrf']
        yield c


def submit(client, **kwargs):
    response = client.post('/api/runs', json={'task': 'Plan a student portfolio launch', **kwargs})
    assert response.status_code == 201
    return response.json()


def test_complete_and_persist(client):
    run = submit(client, approval_required=False)
    assert run['status'] == 'queued'
    job = claim()
    assert job['id'] == run['id']
    assert claim() is None
    process(job, delay=0)
    saved = client.get('/api/runs/' + run['id']).json()
    assert saved['status'] == 'completed'
    assert json.loads(saved['result'])['mode'] == 'demo'
    assert [e['kind'] for e in saved['events']] == ['queued', 'started', 'progress', 'progress', 'progress', 'completed']
    with connect() as fresh:
        assert fresh.execute('SELECT result FROM runs WHERE id=?', (run['id'],)).fetchone()['result'] == saved['result']
    assert client.get('/api/runs/' + run['id'] + '/export').status_code == 200


def test_approval_enforced_and_duplicate_rejected(client):
    run = submit(client)
    process(claim(), delay=0)
    url = '/api/runs/' + run['id']
    assert client.get(url).json()['status'] == 'awaiting_approval'
    assert client.get(url + '/export').status_code == 403
    assert client.post(url + '/approve').json()['status'] == 'completed'
    assert client.post(url + '/approve').status_code == 409
    assert client.post(url + '/reject').status_code == 409
    assert client.get(url + '/export').status_code == 200


def test_rejection_blocks_export(client):
    run = submit(client)
    process(claim(), delay=0)
    url = '/api/runs/' + run['id']
    assert client.post(url + '/reject').json()['status'] == 'rejected'
    assert client.get(url + '/export').status_code == 403


def test_cancel_queued_and_retry(client):
    run = submit(client)
    url = '/api/runs/' + run['id']
    assert client.post(url + '/retry').status_code == 409
    assert client.post(url + '/cancel').json()['status'] == 'cancelled'
    assert claim() is None
    retry = client.post(url + '/retry').json()
    assert retry['id'] != run['id']
    assert retry['parent_id'] == run['id']
    assert client.get(url).json()['status'] == 'cancelled'


def test_running_cancel_not_overwritten(client):
    run = submit(client)
    job = claim()
    client.post('/api/runs/' + run['id'] + '/cancel')
    process(job, delay=0)
    saved = client.get('/api/runs/' + run['id']).json()
    assert saved['status'] == 'cancelled'
    assert saved['result'] is None


def test_recovery_preserves_queued(client):
    first = submit(client)
    claim()
    second = submit(client)
    recover()
    assert client.get('/api/runs/' + first['id']).json()['status'] == 'failed'
    assert client.get('/api/runs/' + second['id']).json()['status'] == 'queued'


def test_validation_and_origin(client):
    for task in ['', '   ', 'x' * 4001]:
        assert client.post('/api/runs', json={'task': task}).status_code == 422
    assert client.get('/api/runs/missing').status_code == 404
    assert client.post('/api/runs', json={'task': 'hello'}, headers={'Origin': 'https://untrusted.example'}).status_code == 403
    assert client.get('/api/health', headers={'Host': 'untrusted.example'}).status_code == 403


def test_event_stream_replays(client):
    run = submit(client, approval_required=False)
    process(claim(), delay=0)
    saved = client.get('/api/runs/' + run['id']).json()
    last = saved['events'][-2]['id']
    response = client.get('/api/runs/' + run['id'] + '/stream', headers={'Last-Event-ID': str(last)})
    assert response.status_code == 200
    assert response.text.count('event: activity') == 1
    assert 'event: done' in response.text


def test_model_failure_is_persisted(client, monkeypatch):
    run = submit(client)
    with connect() as db:
        db.execute("UPDATE runs SET mode='ollama',model='test' WHERE id=?", (run['id'],))
    def fail(*args, **kwargs):
        raise RuntimeError('Model unavailable')
    monkeypatch.setattr('backend.worker.httpx.stream', fail)
    process(claim(), delay=0)
    saved = client.get('/api/runs/' + run['id']).json()
    assert saved['status'] == 'failed'
    assert saved['error'] == 'Model unavailable'


def test_queue_pause_and_agent_disable(client):
    client.post('/api/queue/pause')
    run = submit(client)
    assert claim() is None
    assert client.get('/api/health').json()['paused'] is True
    client.post('/api/queue/resume')
    client.patch('/api/agents/planner', json={'enabled': False})
    assert claim() is None
    assert client.post('/api/runs', json={'task': 'Another task'}).status_code == 409
    client.patch('/api/agents/planner', json={'enabled': True})
    assert claim()['id'] == run['id']


@pytest.mark.parametrize('finished', [True, False])
def test_ollama_stream_result_and_interruption(client, monkeypatch, finished):
    run = submit(client, approval_required=False)
    with connect() as db:
        db.execute("UPDATE runs SET mode='ollama',model='test' WHERE id=?", (run['id'],))
    class Stream:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_lines(self):
            yield json.dumps({'message': {'content': 'A real adapter response.'}, 'done': False})
            if finished:
                yield json.dumps({'done': True, 'prompt_eval_count': 20, 'eval_count': 8})
    monkeypatch.setattr('backend.worker.httpx.stream', lambda *a, **k: Stream())
    process(claim(), delay=0)
    saved = client.get('/api/runs/' + run['id']).json()
    if finished:
        assert saved['status'] == 'completed'
        assert saved['tokens'] == 28
        assert json.loads(saved['result'])['output'] == 'A real adapter response.'
    else:
        assert saved['status'] == 'failed'
        assert saved['result'] is None


def test_concurrent_claims_are_unique(client):
    from concurrent.futures import ThreadPoolExecutor
    run = submit(client)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: claim(), range(4)))
    assert [r['id'] for r in results if r] == [run['id']]
