import json
import math
import pytest
from fastapi.testclient import TestClient
from backend.app import app
from backend.db import connect
from backend.worker import claim, process
from backend.document_qa import ABSTENTION, validate_answer, answer_question, Ollama
from backend.retrieval import Index, chunk_document, cosine
from evaluation.run import retrieval_metrics, score_answer, summarize

TEXT = 'Annual leave allowance is 24 working days per calendar year. Leave requests require manager approval.'
DOC = {'id': 'policy', 'title': 'Leave policy', 'text': TEXT}
PASSWORD = 'a-long-test-password-2026'


def raw_answer(hit):
    return {'abstain': False, 'claims': [{'text': 'Annual leave is 24 working days.',
            'citations': [{'source_id': hit['source_id'], 'quote': 'Annual leave allowance is 24 working days per calendar year.'}]}]}


class FakeProvider:
    model = 'test-model'
    def answer(self, question, hits):
        return raw_answer(hits[0]), {'model': self.model, 'prompt_tokens': 10, 'completion_tokens': 20}


def test_chunks_preserve_original_offsets_and_overlap():
    document = {'id': 'long', 'title': 'Unicode source', 'text': ('This is a paragraph with α and café.\n' * 120)}
    chunks = chunk_document(document)
    assert len(chunks) > 1
    assert len({chunk['id'] for chunk in chunks}) == len(chunks)
    assert chunks == chunk_document(document)
    for chunk in chunks:
        assert chunk['text'] == document['text'][chunk['start']:chunk['end']]
        assert chunk['line_start'] == document['text'].count('\n', 0, chunk['start']) + 1
        assert len(chunk['text']) <= 1200
    for previous, current in zip(chunks, chunks[1:]):
        assert previous['end'] > current['start']
    assert chunks[-1]['end'] == len(document['text'])


def test_bm25_baseline_empty_and_deterministic():
    index = Index([DOC, {'id': 'other', 'title': 'Food', 'text': 'Lunch includes rice and vegetables.'}])
    first = index.search('annual leave')
    assert first['hits'][0]['document_id'] == 'policy'
    assert first['hits'] == index.search('annual leave')['hits']
    assert index.search('xyzzzzzzz')['hits'] == []
    assert Index([]).search('leave')['hits'] == []
    with pytest.raises(ValueError): index.search('leave', 'unknown')
    with pytest.raises(ValueError): index.search('leave', k=11)


def test_hybrid_fuses_semantic_and_lexical_rankings():
    docs = [DOC, {'id': 'backup', 'title': 'Recovery', 'text': 'Restore snapshots every six hours.'}]
    def embed(texts):
        return [[1., 0.], [0., 1.], [1., 0.]]
    index = Index(docs)
    assert index.search('lost files', 'dense', embed)['hits'][0]['document_id'] == 'backup'
    assert index.search('annual leave', 'hybrid', embed)['hits']
    with pytest.raises(ValueError): index.search('leave', 'dense')
    with pytest.raises(ValueError): index.search('leave', 'dense', lambda texts: [])
    for left, right in [([0], [1]), ([1], [1, 2]), ([math.nan], [1])]:
        with pytest.raises(ValueError): cosine(left, right)


def test_quote_provenance_and_fail_closed_answer():
    hit = Index([DOC]).search('annual leave')['hits'][0]
    valid = validate_answer(raw_answer(hit), [hit])
    assert not valid['abstained'] and '[S1]' in valid['output']
    assert valid['evidence'][0]['document_id'] == 'policy'
    for field, value in [('source_id', 'S99'), ('quote', 'Invented evidence that never appeared.')]:
        raw = raw_answer(hit)
        raw['claims'][0]['citations'][0][field] = value
        with pytest.raises(ValueError): validate_answer(raw, [hit])
    raw = raw_answer(hit)
    raw['claims'][0]['text'] = 'Fake link https://madeup.example'
    with pytest.raises(ValueError): validate_answer(raw, [hit])
    raw = raw_answer(hit); raw['abstain'] = True
    with pytest.raises(ValueError): validate_answer(raw, [hit])
    with pytest.raises(ValueError): validate_answer({'abstain': False, 'claims': []}, [hit])
    class Bad(FakeProvider):
        def answer(self, question, hits):
            raw, usage = super().answer(question, hits)
            raw['claims'][0]['citations'][0]['source_id'] = 'S99'
            return raw, usage
    answer = answer_question([DOC], 'annual leave', Bad(), capture_raw=True)
    assert answer['abstained'] and answer['output'] == ABSTENTION
    assert answer['reason'] == 'invalid_citation_or_response'
    assert 'raw_model_response' in answer


def test_empty_retrieval_does_not_call_model():
    class Never(FakeProvider):
        def answer(self, *args): raise AssertionError('No evidence: do not call model.')
    result = answer_question([DOC], 'xyzzzzzz', Never())
    assert result['abstained'] and result['usage']['completion_tokens'] == 0
    assert result['reason'] == 'no_matching_passages'


@pytest.fixture
def users(tmp_path, monkeypatch):
    monkeypatch.setenv('CONTROL_DB', str(tmp_path/'data'/'library.db'))
    monkeypatch.setattr('backend.app.models', lambda: {'models': ['test-model']})
    with TestClient(app) as owner, TestClient(app) as other:
        for client in (owner, other): client.headers['X-Orbit-Request'] = '1'
        setup = owner.post('/api/auth/setup', json={'username': 'owner', 'password': PASSWORD}).json()
        owner.headers['X-Orbit-CSRF'] = setup['csrf']
        owner.post('/api/auth/users', json={'username': 'other', 'password': PASSWORD, 'role': 'member'})
        login = other.post('/api/auth/login', json={'username': 'other', 'password': PASSWORD}).json()
        other.headers['X-Orbit-CSRF'] = login['csrf']
        yield owner, other


def add(client, text=TEXT):
    response = client.post('/api/documents', json={'title': 'Leave policy', 'text': text})
    assert response.status_code == 201
    return response.json()['id']


def task(identifier, **overrides):
    return dict({'task': 'What is the annual leave allowance?', 'mode': 'ollama', 'model': 'test-model',
                 'research_mode': True, 'document_ids': [identifier]}, **overrides)


def test_document_isolation_including_admin_and_deduplication(users):
    admin, member = users
    identifier = add(member)
    assert add(member) == identifier
    assert admin.get('/api/documents').json() == []
    assert admin.get('/api/documents/'+identifier).status_code == 404
    assert admin.delete('/api/documents/'+identifier).status_code == 404
    assert admin.post('/api/runs', json=task(identifier)).status_code == 404
    own = member.get('/api/documents/'+identifier).json()
    assert own['text'] == TEXT and own['chunks'][0]['text'] == TEXT
    assert len(member.get('/api/documents').json()) == 1
    assert add(admin) != identifier
    assert member.delete('/api/documents/'+identifier, headers={'X-Orbit-CSRF': 'wrong'}).status_code == 403


def test_ingestion_limits_and_unknown_fields(users):
    owner, _ = users
    for body in [{'title': 'A', 'text': 'short'}, {'title': ' ', 'text': TEXT},
                 {'title': 'A', 'text': TEXT+'\0'}, {'title': 'A', 'text': TEXT, 'path': '/etc/passwd'},
                 {'title': 'A', 'text': 'a'*40001}, {'title': 'A', 'text': ' '*50+'x'}]:
        assert owner.post('/api/documents', json=body).status_code in (413, 422)
    from backend.library import DocumentInput
    with pytest.raises(ValueError): DocumentInput(title='A', text=TEXT+'\ud800')
    for i in range(30): add(owner, TEXT+str(i))
    assert owner.post('/api/documents', json={'title': 'Over quota', 'text': TEXT+'extra'}).status_code == 429


def test_source_whitespace_and_line_numbers_are_preserved(users):
    owner, _ = users
    original = '\n\n' + TEXT + '\n'
    identifier = add(owner, original)
    source = owner.get('/api/documents/'+identifier).json()
    assert source['text'] == original
    hit = source['chunks'][0]
    assert hit['text'] == original[hit['start']:hit['end']]
    assert hit['line_start'] == 1 and hit['line_end'] == 4


def test_research_inputs_validation(users, monkeypatch):
    owner, _ = users; identifier = add(owner)
    for overrides in [{'mode': 'demo'}, {'enabled_tools': ['web_search']}, {'document_ids': []},
                      {'research_mode': False}, {'retrieval_strategy': 'hybrid'}]:
        monkeypatch.delenv('ORBIT_EMBEDDING_MODEL', raising=False)
        assert owner.post('/api/runs', json=task(identifier, **overrides)).status_code == 400


def test_research_snapshot_worker_export_and_retry(users, monkeypatch):
    owner, other = users; identifier = add(owner)
    response = owner.post('/api/runs', json=task(identifier))
    assert response.status_code == 201
    run = response.json(); run_id = run['id']
    assert run['research']['documents'][0]['sha256']
    assert other.get('/api/runs/'+run_id).status_code == 404
    monkeypatch.setattr(Ollama, 'answer', FakeProvider().answer)
    process(claim(), delay=0)
    detail = owner.get('/api/runs/'+run_id).json()
    assert detail['status'] == 'awaiting_approval'
    assert owner.get('/api/runs/'+run_id+'/export').status_code == 403
    result = json.loads(detail['result'])
    assert '24' in result['output'] and result['evidence'][0]['document_id'] == identifier
    assert owner.post('/api/runs/'+run_id+'/approve').status_code == 200
    exported = owner.get('/api/runs/'+run_id+'/export')
    assert exported.status_code == 200 and '[S1]' in exported.text
    assert 'Supporting evidence' in exported.text and TEXT.split(' Leave')[0] in exported.text
    assert owner.post('/api/runs/'+run_id+'/retry').status_code == 201
    assert owner.delete('/api/documents/'+identifier).status_code == 204
    # Captured evidence survives source removal and remains private.
    assert owner.get('/api/runs/'+run_id).json()['research']['documents']
    assert owner.post('/api/runs/'+run_id+'/retry').status_code == 404


def test_model_failure_is_not_reported_as_success(users, monkeypatch):
    owner, _ = users; identifier = add(owner)
    run = owner.post('/api/runs', json=task(identifier)).json()
    def fail(*args): raise RuntimeError('Local model offline')
    monkeypatch.setattr(Ollama, 'answer', fail)
    process(claim(), delay=0)
    detail = owner.get('/api/runs/'+run['id']).json()
    assert detail['status'] == 'failed' and 'offline' in detail['error']


def test_cancellation_during_research_cannot_commit_result(users, monkeypatch):
    owner, _ = users; identifier = add(owner)
    run = owner.post('/api/runs', json=task(identifier)).json()
    def cancel(self, question, hits):
        with connect() as db: db.execute("UPDATE runs SET status='cancelled' WHERE id=?", (run['id'],))
        return FakeProvider().answer(question, hits)
    monkeypatch.setattr(Ollama, 'answer', cancel)
    process(claim(), delay=0)
    detail = owner.get('/api/runs/'+run['id']).json()
    assert detail['status'] == 'cancelled' and detail['result'] is None


def test_metric_denominators_and_lexical_proxy_limits():
    hits = [{'document_id': 'a'}, {'document_id': 'a'}, {'document_id': 'b'}]
    assert retrieval_metrics(hits, ['a', 'b']) == {'recall_at_k': 1, 'mrr': 1}
    assert retrieval_metrics(hits, ['c']) == {'recall_at_k': 0, 'mrr': 0}
    assert retrieval_metrics(hits, [])['recall_at_k'] is None
    rows = [{'error': 'timeout', 'expected_abstention': False, 'recall_at_k': 0, 'mrr': 0},
            {'retrieval_ms': 1, 'total_ms': 2, 'expected_abstention': True, 'abstained': True,
             'automatic_fact_or_abstention_pass': True, 'recall_at_k': None, 'mrr': None}]
    summary = summarize(rows, True)
    assert summary['errors'] == 1 and summary['automatic_fact_or_abstention_pass_rate'] == .5
    assert summary['answerable_fact_pass_rate'] == 0 and summary['unanswerable_abstention_rate'] == 1
