"""Single local worker with durable state, bounded model calls and explicit approval."""
import fcntl
import json
import os
import signal
import threading
import time
from pathlib import Path
import httpx
from .db import connect, init, event, now

stopping = threading.Event()
PROMPTS = {
    'planner': 'You are a practical planning assistant. Produce a concise structured plan with objective, ordered steps, deliverables, and acceptance checks. Do not claim to execute actions or browse.',
    'writer': 'You are a writing assistant. Produce a polished structured draft following the user instructions. Do not invent sources or claim external actions.',
    'analyst': 'You are an analysis assistant. Analyze the supplied material, separate facts from assumptions, identify missing information, and propose practical next steps. Do not claim to browse or verify external facts.'
}


def claim():
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute("SELECT value FROM settings WHERE key='queue_paused'").fetchone()['value'] == '1':
            return None
        row = db.execute("SELECT runs.* FROM runs JOIN agents ON agents.id=runs.agent WHERE runs.status='queued' AND agents.enabled=1 ORDER BY runs.created_at LIMIT 1").fetchone()
        if row is None:
            return None
        db.execute("UPDATE runs SET status='running',updated_at=? WHERE id=?", (now(), row['id']))
        event(db, row['id'], 'started', f"{row['agent'].capitalize()} agent claimed this task ({row['mode']}).")
        return dict(row)


def heartbeat():
    with connect() as db:
        db.execute("INSERT INTO workers VALUES('local',?) ON CONFLICT(id) DO UPDATE SET heartbeat=excluded.heartbeat", (now(),))


def alive(run_id):
    with connect() as db:
        row = db.execute('SELECT status FROM runs WHERE id=?', (run_id,)).fetchone()
        return bool(row and row['status'] == 'running')


def progress(run_id, message):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT status FROM runs WHERE id=?', (run_id,)).fetchone()
        if row and row['status'] == 'running':
            event(db, run_id, 'progress', message)


def process(run, delay=0.8):
    try:
        progress(run['id'], 'Reading the task input and agent instructions.')
        time.sleep(delay)
        if not alive(run['id']):
            return
        with connect() as db:
            research = db.execute('SELECT * FROM research_inputs WHERE run_id=?', (run['id'],)).fetchone()
        if research:
            from .document_qa import Ollama, answer_question
            progress(run['id'], 'Retrieving private document passages and checking answer citations.')
            provider = Ollama(run['model'], research['embedding_model'])
            result = answer_question(json.loads(research['snapshot']), run['task'], provider, research['strategy'])
            result['usage']['embedding_requests'] = provider.embedding_requests
            result['usage']['embedding_tokens'] = provider.embedding_tokens
            finish(run, result, result['usage']['prompt_tokens'] + result['usage']['completion_tokens'])
            return
        if run['mode'] == 'ollama' and json.loads(run.get('enabled_tools') or '[]'):
            from .agent_runtime import run_agent
            outcome = run_agent(run)
            if outcome is not None:
                result, tokens = outcome
                finish(run, result, tokens)
            return
        tokens = 0
        if run['mode'] == 'ollama':
            progress(run['id'], 'Generating with local model ' + run['model'] + '. No external tools are enabled.')
            endpoint = os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434')
            output = ''
            finished = False
            deadline = time.monotonic() + 300
            with httpx.stream('POST', endpoint + '/api/chat', json={
                'model': run['model'], 'stream': True, 'think': False,
                'messages': [{'role': 'system', 'content': PROMPTS[run['agent']]}, {'role': 'user', 'content': run['task']}],
                'options': {'num_predict': 1536, 'temperature': 0.4}
            }, timeout=httpx.Timeout(60, connect=5)) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not alive(run['id']):
                        return
                    if time.monotonic() > deadline:
                        raise TimeoutError('Model exceeded the five-minute run limit.')
                    if not line:
                        continue
                    part = json.loads(line)
                    if part.get('error'):
                        raise RuntimeError(part['error'])
                    output += part.get('message', {}).get('content', '')
                    if part.get('done'):
                        finished = True
                        tokens = part.get('prompt_eval_count', 0) + part.get('eval_count', 0)
            if not finished:
                raise RuntimeError('Model stream ended before completion. Retry this task.')
            if not output.strip():
                raise RuntimeError('The model returned an empty response.')
            result = {'title': run['agent'].capitalize() + ' report', 'output': output, 'mode': 'ollama'}
        else:
            progress(run['id'], 'Preparing a deterministic task brief. No AI model is being called.')
            time.sleep(delay)
            result = {'title': 'Task brief ready', 'task': run['task'],
                'summary': 'Your task was processed by the local demo worker. The queue, event history, and result storage completed end to end.',
                'next_steps': ['Define the desired output and acceptance criteria.', 'Collect the relevant input and sources.', 'Use a local Ollama model to generate a substantive response.'],
                'word_count': len(run['task'].split()), 'mode': 'demo'}
        progress(run['id'], 'Saving the generated draft and usage record.')
        time.sleep(delay)
        finish(run, result, tokens)
    except Exception as exc:
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT status FROM runs WHERE id=?', (run['id'],)).fetchone()
            if current and current['status'] == 'running':
                db.execute("UPDATE runs SET status='failed',error=?,updated_at=? WHERE id=?", (str(exc), now(), run['id']))
                event(db, run['id'], 'failed', 'Processing failed. See the error details; retry creates a new attempt.')


def finish(run, result, tokens):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        current = db.execute('SELECT status FROM runs WHERE id=?', (run['id'],)).fetchone()
        if current['status'] != 'running':
            return
        status = 'awaiting_approval' if run['approval_required'] else 'completed'
        db.execute('UPDATE runs SET status=?,result=?,tokens=?,updated_at=? WHERE id=?', (status, json.dumps(result), tokens, now(), run['id']))
        event(db, run['id'], status, 'Draft stored. Your approval is required to enable report export.' if run['approval_required'] else 'Result stored successfully. Report export is enabled.')


def recover():
    with connect() as db:
        db.execute("UPDATE actions SET status='unknown',error='Worker interrupted during dispatch. Verify the destination; this action will not be repeated.',finished_at=? WHERE status='executing'", (now(),))
        for row in db.execute("SELECT id FROM runs WHERE status='running'").fetchall():
            db.execute("UPDATE runs SET status='failed',error=?,updated_at=? WHERE id=?", ('Worker interrupted; retry to create a new attempt.', now(), row['id']))
            event(db, row['id'], 'failed', 'Previous worker stopped before completion.')


def main():
    init()
    lock_path = Path(os.environ.get('CONTROL_DB', 'data/control.db')).with_suffix('.worker.lock')
    with lock_path.open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('A worker is already running for this database.')
        recover()
        signal.signal(signal.SIGTERM, lambda *_: stopping.set())
        signal.signal(signal.SIGINT, lambda *_: stopping.set())
        def pulse():
            while not stopping.is_set():
                heartbeat()
                stopping.wait(2)
        thread = threading.Thread(target=pulse, daemon=True)
        thread.start()
        print('Orbit worker ready', flush=True)
        try:
            while not stopping.is_set():
                run = claim()
                if run:
                    process(run)
                else:
                    stopping.wait(0.5)
        finally:
            stopping.set()
            thread.join(timeout=3)
            with connect() as db:
                db.execute("DELETE FROM workers WHERE id='local'")


if __name__ == '__main__':
    main()
