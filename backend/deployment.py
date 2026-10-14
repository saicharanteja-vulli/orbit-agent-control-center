"""Fail-closed production configuration and bounded request/rate controls."""
import hashlib
import os
import time
from urllib.parse import urlsplit
from fastapi import HTTPException
from .db import connect


def production():
    return os.environ.get('ORBIT_ENV','local') == 'production'


def public_origin():
    return os.environ.get('ORBIT_PUBLIC_URL','').rstrip('/')


def validate_config():
    if not production(): return
    url=urlsplit(public_origin())
    if url.scheme!='https' or not url.hostname or url.hostname in {'localhost','127.0.0.1'} or url.username or url.password or url.path or url.query or url.fragment:
        raise RuntimeError('Production requires ORBIT_PUBLIC_URL=https://your-public-domain with no path or credentials.')
    if os.environ.get('ORBIT_SECURE_COOKIE')!='1':
        raise RuntimeError('Production requires ORBIT_SECURE_COOKIE=1.')
    if not os.environ.get('CONTROL_DB','').startswith('/'):
        raise RuntimeError('Production requires an absolute persistent CONTROL_DB path.')


def throttle(key, limit, seconds):
    stamp=int(time.time())//seconds
    key=hashlib.sha256(key.encode()).hexdigest()+':'+str(seconds)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM rate_limits WHERE expires<?',(time.time(),))
        row=db.execute('SELECT count FROM rate_limits WHERE key=? AND window=?',(key,stamp)).fetchone()
        if row and row['count']>=limit:
            raise HTTPException(429,'Request limit reached. Please try again later.',headers={'Retry-After':str(seconds)})
        db.execute('INSERT INTO rate_limits VALUES(?,?,1,?) ON CONFLICT(key,window) DO UPDATE SET count=count+1',(key,stamp,(stamp+1)*seconds))


class BodyLimit:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope['method'] in {'GET','HEAD','OPTIONS'}:
            return await self.app(scope,receive,send)
        body=bytearray()
        while True:
            message=await receive()
            if message['type']=='http.disconnect':return
            body.extend(message.get('body',b''))
            if len(body)>65536:
                from starlette.responses import JSONResponse
                return await JSONResponse({'detail':'Request body exceeds 64 KB.'},status_code=413)(scope,receive,send)
            if not message.get('more_body'):break
        consumed=False
        async def replay():
            nonlocal consumed
            if not consumed:
                consumed=True
                return {'type':'http.request','body':bytes(body),'more_body':False}
            return await receive()
        await self.app(scope,replay,send)
