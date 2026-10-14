import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from .db import connect, init, event, now

from .deployment import production, public_origin, validate_config, BodyLimit
from .auth import router as auth_router, current_user, session

OLLAMA = os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434')


@asynccontextmanager
async def lifespan(app):
    validate_config()
    init()
    yield


app = FastAPI(title='Orbit Agent Control Center', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(BodyLimit)


@app.middleware('http')
async def local_only(request: Request, call_next):
    from urllib.parse import urlsplit
    host = request.headers.get('host', '')
    expected = urlsplit(public_origin()).netloc if production() else None
    if (production() and host != expected) or (not production() and host.split(':')[0] not in {'127.0.0.1','localhost','testserver'}):
        return Response('Host not allowed',status_code=403)
    origin=request.headers.get('origin')
    if request.method not in {'GET','HEAD','OPTIONS'}:
        if request.headers.get('sec-fetch-site')=='cross-site':
            return Response('Cross-site requests are not allowed',status_code=403)
        permitted = public_origin() if production() else str(request.url).split('/')[0]+'//'+host
        if origin and origin != permitted:
            return Response('Cross-origin requests are not allowed',status_code=403)
    path = request.url.path
    mutation = request.method not in {'GET','HEAD','OPTIONS'}
    public_auth = path in {'/api/auth/status','/api/auth/login','/api/auth/setup','/api/auth/signup'}
    user = None
    if path.startswith('/api/'):
        if mutation and request.headers.get('x-orbit-request') != '1':
            return JSONResponse({'detail':'Missing same-origin request header.'},status_code=403)
        if not public_auth:
            user = session(request)
            if not user:
                return JSONResponse({'detail':'Sign in to continue.'},status_code=401)
            if mutation:
                import hmac
                if not hmac.compare_digest(request.headers.get('x-orbit-csrf',''),user['csrf']):
                    return JSONResponse({'detail':'Session verification failed. Sign in again.'},status_code=403)
                if user['role']=='viewer' and path not in {'/api/auth/logout','/api/auth/password'}:
                    return JSONResponse({'detail':'Viewer accounts are read-only.'},status_code=403)
            admin_route = path.startswith('/api/auth/users') or path.startswith('/api/queue/') or (mutation and (path.startswith('/api/agents/') or path=='/api/integrations/webhook'))
            if admin_route and user['role']!='admin':
                return JSONResponse({'detail':'Administrator access required.'},status_code=403)
            parts=path.split('/')
            if len(parts)>3 and parts[2] in {'runs','actions'}:
                with connect() as db:
                    if parts[2]=='runs':
                        owner=db.execute('SELECT owner_id FROM runs WHERE id=?',(parts[3],)).fetchone()
                    else:
                        owner=db.execute('SELECT r.owner_id FROM actions a JOIN runs r ON r.id=a.run_id WHERE a.id=?',(parts[3],)).fetchone()
                if not owner or owner['owner_id']!=user['id']:
                    return JSONResponse({'detail':'Not found.'},status_code=404)
    token=current_user.set(user)
    try:
        response = await call_next(request)
    finally:
        current_user.reset(token)
    if path.startswith('/api/'):
        response.headers['Cache-Control']='no-store'

    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    response.headers['X-Frame-Options']='DENY'
    response.headers['Permissions-Policy']='camera=(), microphone=(), geolocation=()'
    if production(): response.headers['Strict-Transport-Security']='max-age=31536000'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


class TaskInput(BaseModel):
    task: str = Field(min_length=3, max_length=4000)
    agent: Literal['planner', 'writer', 'analyst'] = 'planner'
    mode: Literal['demo', 'ollama'] = 'demo'
    model: str = Field(default='', max_length=120)
    approval_required: bool = True
    enabled_tools: list[Literal['web_search', 'read_webpage', 'save_file', 'send_webhook']] = Field(default_factory=list, max_length=4)

    @field_validator('task')
    @classmethod
    def clean(cls, value):
        value = value.strip()
        if len(value) < 3:
            raise ValueError('Please enter at least three non-space characters.')
        return value


@app.get('/api/health')
def health():
    with connect() as db:
        row = db.execute('SELECT heartbeat FROM workers ORDER BY heartbeat DESC LIMIT 1').fetchone()
    online = bool(row and (datetime.now(timezone.utc) - datetime.fromisoformat(row['heartbeat'])).total_seconds() < 10)
    with connect() as db:
        paused = db.execute("SELECT value FROM settings WHERE key='queue_paused'").fetchone()['value'] == '1'
    return {'api': 'online', 'worker': 'online' if online else 'offline', 'paused': paused}


@app.get('/api/models')
def models():
    try:
        response = httpx.get(OLLAMA + '/api/tags', timeout=2)
        response.raise_for_status()
        return {'models': [m['name'] for m in response.json()['models']], 'available': True}
    except (httpx.HTTPError, KeyError, ValueError):
        return {'models': [], 'available': False}


@app.get('/api/agents')
def agents():
    with connect() as db:
        return [dict(row) for row in db.execute('SELECT * FROM agents ORDER BY id')]


class EnabledInput(BaseModel):
    enabled: bool


@app.patch('/api/agents/{agent_id}')
def configure_agent(agent_id: str, body: EnabledInput):
    with connect() as db:
        cursor = db.execute('UPDATE agents SET enabled=? WHERE id=?', (int(body.enabled), agent_id))
        if cursor.rowcount != 1:
            raise HTTPException(404, 'Agent not found')
    return {'id': agent_id, 'enabled': body.enabled}


@app.post('/api/queue/{action}')
def queue_control(action: Literal['pause', 'resume']):
    with connect() as db:
        db.execute("UPDATE settings SET value=? WHERE key='queue_paused'", ('1' if action == 'pause' else '0',))
    return health()


def create_run(body, parent_id=None):
    owner = current_user.get()
    if not owner: raise HTTPException(401, 'Sign in to continue.')
    if 'send_webhook' in body.enabled_tools and owner['role']!='admin':
        raise HTTPException(403, 'Only administrators may use the shared webhook.')
    with connect() as db:
        configured = db.execute('SELECT enabled FROM agents WHERE id=?', (body.agent,)).fetchone()
        if not configured or not configured['enabled']:
            raise HTTPException(409, 'This agent is disabled. Enable it from the Agents page.')
    if body.mode == 'demo' and body.enabled_tools:
        raise HTTPException(400, 'Tools require a real Ollama model. Demo mode never executes tools.')
    if body.mode == 'ollama' and body.model not in models()['models']:
        raise HTTPException(400, 'Selected model is not available. Start Ollama and refresh the page.')
    run_id, stamp = str(uuid4()), now()
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if production():
            counts=db.execute("SELECT COUNT(*) total,SUM(status IN ('queued','running','awaiting_approval')) active,SUM(created_at>=?) daily FROM runs WHERE owner_id=?",(datetime.now(timezone.utc).date().isoformat(),owner['id'])).fetchone()
            global_pending=db.execute("SELECT COUNT(*) FROM runs WHERE status IN ('queued','running')").fetchone()[0]
            if counts['total']>=100 or (counts['active'] or 0)>=3 or (counts['daily'] or 0)>=20 or global_pending>=20:
                raise HTTPException(429,'Task limit reached (3 unfinished, 20/day, 100 total per account; shared queue 20).')
        db.execute('INSERT INTO runs(id,task,status,created_at,updated_at,agent,mode,model,approval_required,parent_id,enabled_tools,owner_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                   (run_id, body.task, 'queued', stamp, stamp, body.agent, body.mode, body.model, int(body.approval_required), parent_id, json.dumps(list(dict.fromkeys(body.enabled_tools))), owner['id']))
        event(db, run_id, 'queued', 'Task saved. Waiting for the worker.' if not parent_id else 'New attempt queued; original history preserved.')
    return detail(run_id)


@app.post('/api/runs', status_code=201)
def submit(body: TaskInput):
    return create_run(body)


@app.get('/api/runs')
def runs():
    with connect() as db:
        return [{key: row[key] for key in row.keys() if key != 'checkpoint'} for row in db.execute('SELECT * FROM runs WHERE owner_id=? ORDER BY created_at DESC LIMIT 100', (current_user.get()['id'],))]


@app.get('/api/runs/{run_id}')
def detail(run_id: str):
    with connect() as db:
        row = db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
        if row is None:
            raise HTTPException(404, 'Run not found')
        result = dict(row)
        result['events'] = [dict(e) for e in db.execute('SELECT * FROM events WHERE run_id=? ORDER BY id', (run_id,))]
        result['actions'] = [dict(a) for a in db.execute('SELECT * FROM actions WHERE run_id=? ORDER BY sequence', (run_id,))]
        result.pop('checkpoint', None)
        return result


@app.post('/api/runs/{run_id}/cancel')
def cancel(run_id: str):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT status FROM runs WHERE id=?', (run_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Run not found')
        if row['status'] not in {'queued', 'running', 'awaiting_approval'}:
            raise HTTPException(409, 'This run is already finished.')
        db.execute("UPDATE runs SET status='cancelled',updated_at=? WHERE id=?", (now(), run_id))
        db.execute("UPDATE actions SET status='cancelled',finished_at=? WHERE run_id=? AND status IN ('pending','approved','ready')", (now(), run_id))
        event(db, run_id, 'cancelled', 'Cancelled by you. Worker output will not be committed; an in-flight model call may finish in the background.')
    return detail(run_id)


def decision(run_id, approved):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT status FROM runs WHERE id=?', (run_id,)).fetchone()
        if not row:
            raise HTTPException(404, 'Run not found')
        if row['status'] != 'awaiting_approval':
            raise HTTPException(409, 'This run is not waiting for approval.')
        if db.execute("SELECT id FROM actions WHERE run_id=? AND status='pending'", (run_id,)).fetchone():
            raise HTTPException(409, 'Review and approve the specific tool action, not report export.')
        if not db.execute('SELECT result FROM runs WHERE id=?', (run_id,)).fetchone()['result']:
            raise HTTPException(409, 'No report is ready for export approval.')
        status = 'completed' if approved else 'rejected'
        db.execute('UPDATE runs SET status=?,updated_at=? WHERE id=?', (status, now(), run_id))
        event(db, run_id, status, 'You approved export of this exact report.' if approved else 'You rejected report export. The draft remains in the audit history.')
    return detail(run_id)


@app.post('/api/runs/{run_id}/approve')
def approve(run_id: str):
    return decision(run_id, True)


@app.post('/api/runs/{run_id}/reject')
def reject(run_id: str):
    return decision(run_id, False)


@app.post('/api/runs/{run_id}/retry', status_code=201)
def retry(run_id: str):
    old = detail(run_id)
    old['enabled_tools'] = json.loads(old.get('enabled_tools') or '[]')
    if old['status'] in {'queued', 'running', 'awaiting_approval'}:
        raise HTTPException(409, 'Finish or cancel the current attempt first.')
    return create_run(TaskInput(**{k: old[k] for k in TaskInput.model_fields}), parent_id=run_id)


@app.get('/api/runs/{run_id}/export')
def export(run_id: str):
    run = detail(run_id)
    if run['status'] != 'completed':
        raise HTTPException(403, 'Report export requires a completed, approved run.')
    result = json.loads(run['result'])
    report = f"# {result['title']}\n\nTask: {run['task']}\n\n{result.get('output', result.get('summary', ''))}\n\n"
    report += '\n'.join('- ' + s for s in result.get('next_steps', []))
    return Response(report, media_type='text/markdown', headers={'Content-Disposition': f'attachment; filename="orbit-{run_id[:8]}.md"'})


@app.get('/api/tools')
def available_tools():
    from .tools import REGISTRY
    return [{'name': name, 'description': item[1], 'requires_approval': item[2]} for name, item in REGISTRY.items()]


class WebhookConfig(BaseModel):
    url: str = Field(max_length=2048)


@app.get('/api/integrations/webhook')
def webhook_config():
    with connect() as db:
        row = db.execute("SELECT value FROM settings WHERE key='webhook_url'").fetchone()
    from urllib.parse import urlsplit
    value = row['value'] if row else ''
    return {'configured': bool(value), 'host': urlsplit(value).hostname if value else None}


@app.put('/api/integrations/webhook')
def set_webhook_config(body: WebhookConfig):
    from .tools import public_target
    url = body.url.strip()
    if url:
        try:
            public_target(url)
        except (ValueError, OSError) as exc:
            raise HTTPException(400, 'Use a reachable public HTTPS webhook URL. Local and private addresses are blocked.') from exc
    with connect() as db:
        db.execute("INSERT INTO settings(key,value) VALUES('webhook_url',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (url,))
        # Editing/disconnecting a destination invalidates unexecuted proposals to the old connection.
        pending = db.execute("SELECT id,run_id FROM actions WHERE tool='send_webhook' AND status IN ('pending','approved')").fetchall()
        for action in pending:
            db.execute("UPDATE actions SET status='rejected',decided_at=? WHERE id=?", (now(), action['id']))
            db.execute("UPDATE runs SET status='queued',updated_at=? WHERE id=? AND status='awaiting_approval'", (now(), action['run_id']))
            event(db, action['run_id'], 'tool_rejected', 'Webhook connection changed; the old unexecuted proposal was invalidated.')
    return webhook_config()


class ActionDecision(BaseModel):
    approved: bool
    digest: str = Field(pattern=r'^[a-f0-9]{64}$')


@app.post('/api/actions/{action_id}/decision')
def action_decision(action_id: str, body: ActionDecision):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        action = db.execute('SELECT * FROM actions WHERE id=?', (action_id,)).fetchone()
        if not action:
            raise HTTPException(404, 'Action not found')
        run = db.execute('SELECT status FROM runs WHERE id=?', (action['run_id'],)).fetchone()
        if action['status'] != 'pending' or run['status'] != 'awaiting_approval':
            raise HTTPException(409, 'This proposal is no longer waiting for approval.')
        if body.digest != action['digest']:
            raise HTTPException(409, 'The displayed proposal does not match. Refresh before deciding.')
        status = 'approved' if body.approved else 'rejected'
        db.execute('UPDATE actions SET status=?,decided_at=? WHERE id=?', (status, now(), action_id))
        db.execute("UPDATE runs SET status='queued',updated_at=? WHERE id=?", (now(), action['run_id']))
        event(db, action['run_id'], 'tool_' + status, f"You {status} {action['tool']} with these exact arguments. Agent queued to continue.")
    return detail(action['run_id'])


@app.get('/api/actions/{action_id}/artifact')
def artifact(action_id: str):
    from .tools import saved_path
    with connect() as db:
        action = db.execute('SELECT * FROM actions WHERE id=?', (action_id,)).fetchone()
    if not action or action['tool'] != 'save_file' or action['status'] != 'succeeded':
        raise HTTPException(404, 'No completed file for this action')
    args = json.loads(action['arguments'])
    target = saved_path(action['run_id'], action_id, args['filename'])
    if not target.is_file() or target.is_symlink():
        raise HTTPException(404, 'Saved file is missing')
    return FileResponse(target, media_type='text/plain', filename=args['filename'])


@app.get('/api/runs/{run_id}/stream')
async def stream(run_id: str, request: Request):
    detail(run_id)
    try:
        last = int(request.headers.get('last-event-id', '0'))
    except ValueError:
        last = 0
    async def events():
        nonlocal last
        while not await request.is_disconnected():
            if not session(request):
                return
            state = detail(run_id)
            for item in state['events']:
                if item['id'] > last:
                    last = item['id']
                    yield f"id: {last}\nevent: activity\ndata: {json.dumps(item)}\n\n"
            if state['status'] not in {'queued', 'running'}:
                yield f"event: done\ndata: {json.dumps({'status': state['status']})}\n\n"
                return
            yield ': keepalive\n\n'
            await asyncio.sleep(1)
    return StreamingResponse(events(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache'})


app.include_router(auth_router)

frontend = Path(__file__).resolve().parents[1] / 'frontend' / 'dist'
if frontend.exists():
    app.mount('/', StaticFiles(directory=frontend, html=True), name='dashboard')
