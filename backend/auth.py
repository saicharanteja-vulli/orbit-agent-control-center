"""Local accounts, opaque revocable sessions, and server-side access policy."""
import hashlib
import hmac
import os
import secrets
import time
from contextvars import ContextVar
from uuid import uuid4
from typing import Literal
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from .db import connect
from .deployment import production, throttle

router = APIRouter(prefix='/api/auth')
current_user = ContextVar('orbit_user', default=None)
COOKIE = 'orbit_session'
TTL = 8 * 60 * 60


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return salt + ':' + digest


def public(row):
    return {k: row[k] for k in ('id','username','role','active')}


def session(request):
    token = request.cookies.get(COOKIE, '')
    if not token:
        return None
    with connect() as db:
        row = db.execute('SELECT u.*,s.csrf,s.expires FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires>? AND u.active=1', (hashlib.sha256(token.encode()).hexdigest(),time.time())).fetchone()
    return dict(row) if row else None


def issue(response, user):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with connect() as db:
        db.execute('DELETE FROM sessions WHERE expires<=?', (time.time(),))
        db.execute('DELETE FROM sessions WHERE user_id=? AND token_hash NOT IN (SELECT token_hash FROM sessions WHERE user_id=? ORDER BY expires DESC LIMIT 4)',(user['id'],user['id']))
        db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),user['id'],csrf,time.time()+TTL))
    response.set_cookie(COOKIE,token,max_age=TTL,httponly=True,samesite='strict',secure=os.environ.get('ORBIT_SECURE_COOKIE')=='1',path='/')
    return {'user':public(user),'csrf':csrf}


class Credentials(BaseModel):
    username: str = Field(min_length=3,max_length=60,pattern=r'^[a-zA-Z0-9_.-]+$')
    password: str = Field(min_length=12,max_length=128)


class NewUser(Credentials):
    role: Literal['admin','member','viewer'] = 'member'


@router.get('/status')
def status():
    with connect() as db:
        return {'setup_required':not production() and db.execute('SELECT COUNT(*) FROM users').fetchone()[0]==0,'signup_enabled':os.environ.get('ORBIT_SIGNUP_ENABLED')=='1' and bool(db.execute('SELECT 1 FROM users LIMIT 1').fetchone())}


@router.post('/setup')
def setup(body: Credentials, response: Response):
    if production(): raise HTTPException(403,'Use the server administrator command to initialize production.')
    hashed = password_hash(body.password)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            raise HTTPException(409,'Setup already completed. Sign in instead.')
        uid=str(uuid4())
        db.execute('INSERT INTO users VALUES(?,?,?,?,1)',(uid,body.username.lower(),hashed,'admin'))
        # Only the first local administrator receives pre-login legacy runs.
        db.execute('UPDATE runs SET owner_id=? WHERE owner_id IS NULL',(uid,))
        user=dict(db.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())
    return issue(response,user)


@router.post('/login')
def login(body: Credentials, response: Response, request: Request):
    throttle('login-ip:'+client_ip(request),20,60)
    username=body.username.lower()
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM login_attempts WHERE created<?',(time.time()-300,))
        if db.execute('SELECT COUNT(*) FROM login_attempts WHERE username=?',(username,)).fetchone()[0]>=5:
            raise HTTPException(429,'Too many attempts. Try again in five minutes.')
        db.execute('INSERT INTO login_attempts VALUES(?,?)',(username,time.time()))
        row=db.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
    hashed=password_hash(body.password,row['password_hash'].split(':')[0] if row else '00'*16)
    if not row or not row['active'] or not hmac.compare_digest(hashed,row['password_hash']):
        raise HTTPException(401,'Invalid username or password.')
    with connect() as db:
        db.execute('DELETE FROM login_attempts WHERE username=?',(username,))
    return issue(response,row)


@router.get('/me')
def me(request: Request):
    user=session(request)
    if not user:
        raise HTTPException(401,'Sign in to continue.')
    return {'user':public(user),'csrf':user['csrf']}


@router.post('/logout')
def logout(request: Request,response: Response):
    with connect() as db:
        db.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(request.cookies.get(COOKIE,'').encode()).hexdigest(),))
    response.delete_cookie(COOKIE,path='/')
    return {'ok':True}


@router.get('/users')
def users():
    with connect() as db:
        return [public(r) for r in db.execute('SELECT * FROM users ORDER BY username')]


@router.post('/users',status_code=201)
def add_user(body: NewUser):
    import sqlite3
    try:
        with connect() as db:
            uid=str(uuid4())
            db.execute('INSERT INTO users VALUES(?,?,?,?,1)',(uid,body.username.lower(),password_hash(body.password),body.role))
            return public(db.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())
    except sqlite3.IntegrityError:
        raise HTTPException(409,'Username already exists.')


class Access(BaseModel):
    role: Literal['admin','member','viewer']
    active: bool


@router.patch('/users/{uid}')
def change_access(uid: str, body: Access):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
        if not row: raise HTTPException(404,'User not found')
        if uid==current_user.get()['id']:
            raise HTTPException(409,'Use another administrator to change your own access.')
        db.execute('UPDATE users SET role=?,active=? WHERE id=?',(body.role,int(body.active),uid))
        db.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
    return {'ok':True}


def client_ip(request):
    # Shared demo bucket prevents spoofed forwarding headers bypassing rate limits.
    if production() and os.environ.get('ORBIT_QUICK_TUNNEL') == '1':
        return 'quick-tunnel'
    # Production API is reachable only from Caddy, which overwrites this header.
    return request.headers.get('x-orbit-client-ip','unknown') if production() else (request.client.host if request.client else 'local')


@router.post('/signup',status_code=201)
def signup(body: Credentials,response: Response,request: Request):
    import sqlite3
    if os.environ.get('ORBIT_SIGNUP_ENABLED')!='1':
        raise HTTPException(403,'Public signup is closed.')
    # Quick Tunnel uses one shared bucket for all visitors; retain the global cap.
    signup_limit = 20 if production() and os.environ.get('ORBIT_QUICK_TUNNEL') == '1' else 3
    throttle('signup-ip:'+client_ip(request),signup_limit,3600)
    throttle('signup-global',20,3600)
    hashed=password_hash(body.password)
    try:
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            count=db.execute('SELECT COUNT(*) FROM users').fetchone()[0]
            if not count: raise HTTPException(503,'The administrator must initialize this workspace first.')
            if count>=int(os.environ.get('ORBIT_MAX_USERS','100')): raise HTTPException(429,'Account capacity reached. Contact the administrator.')
            uid=str(uuid4())
            db.execute('INSERT INTO users VALUES(?,?,?,?,1)',(uid,body.username.lower(),hashed,'member'))
            user=dict(db.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone())
    except sqlite3.IntegrityError:
        raise HTTPException(409,'Username unavailable.')
    return issue(response,user)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=12,max_length=128)
    new_password: str = Field(min_length=12,max_length=128)


@router.post('/password')
def change_password(body: PasswordChange,request: Request,response: Response):
    user=session(request)
    throttle('password:'+user['id'],5,300)
    if not hmac.compare_digest(password_hash(body.current_password,user['password_hash'].split(':')[0]),user['password_hash']):
        raise HTTPException(400,'Current password is incorrect.')
    with connect() as db:
        db.execute('UPDATE users SET password_hash=? WHERE id=?',(password_hash(body.new_password),user['id']))
        db.execute('DELETE FROM sessions WHERE user_id=?',(user['id'],))
    return issue(response,user)
