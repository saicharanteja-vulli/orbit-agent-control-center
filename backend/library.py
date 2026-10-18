"""Private text documents; no arbitrary filesystem paths or URL imports."""
import hashlib
from uuid import uuid4
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .auth import current_user
from .db import connect, now
from .retrieval import chunk_document

router = APIRouter(prefix='/api/documents')


class DocumentInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=50, max_length=40000)

    @field_validator('title', 'text')
    @classmethod
    def clean(cls, value, info):
        if not value.strip() or '\x00' in value:
            raise ValueError('Provide readable text without null characters.')
        value = value.strip()
        if info.field_name == 'text' and len(value) < 50:
            raise ValueError('Document text requires at least 50 non-padding characters.')
        try:
            value.encode('utf-8')
        except UnicodeEncodeError as exc:
            raise ValueError('Document text must be valid UTF-8.') from exc
        return value


@router.get('')
def documents():
    with connect() as db:
        rows = db.execute('SELECT id,title,sha256,created_at,length(text) AS characters FROM documents WHERE owner_id=? ORDER BY created_at DESC', (current_user.get()['id'],)).fetchall()
    return [dict(row) for row in rows]


@router.post('', status_code=201)
def add_document(body: DocumentInput):
    if len(body.text.encode()) > 60000:
        raise HTTPException(413, 'Document text must fit within 60 KB of UTF-8 text.')
    owner = current_user.get()['id']
    checksum = hashlib.sha256(body.text.encode()).hexdigest()
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute('SELECT id,title,sha256,created_at,length(text) AS characters FROM documents WHERE owner_id=? AND sha256=?', (owner, checksum)).fetchone()
        if existing:
            return dict(existing)
        count, chars = db.execute('SELECT count(*),coalesce(sum(length(text)),0) FROM documents WHERE owner_id=?', (owner,)).fetchone()
        if count >= 30 or chars + len(body.text) > 500000:
            raise HTTPException(429, 'Library limit reached: 30 documents or 500,000 characters per account.')
        identifier, stamp = str(uuid4()), now()
        db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?)', (identifier, owner, body.title, body.text, checksum, stamp))
    return {'id': identifier, 'title': body.title, 'sha256': checksum, 'created_at': stamp, 'characters': len(body.text)}


@router.get('/{document_id}')
def read_document(document_id: str):
    with connect() as db:
        row = db.execute('SELECT * FROM documents WHERE id=? AND owner_id=?', (document_id, current_user.get()['id'])).fetchone()
    if not row:
        raise HTTPException(404, 'Document not found.')
    doc = dict(row)
    doc.pop('owner_id')
    doc['chunks'] = chunk_document(doc)
    return doc


@router.delete('/{document_id}', status_code=204)
def delete_document(document_id: str):
    with connect() as db:
        cursor = db.execute('DELETE FROM documents WHERE id=? AND owner_id=?', (document_id, current_user.get()['id']))
        if not cursor.rowcount:
            raise HTTPException(404, 'Document not found.')


def snapshot(db, owner, identifiers):
    if not identifiers:
        raise HTTPException(400, 'Select at least one document for research.')
    result = []
    for identifier in dict.fromkeys(identifiers):
        row = db.execute('SELECT id,title,text,sha256 FROM documents WHERE id=? AND owner_id=?', (identifier, owner)).fetchone()
        if not row:
            raise HTTPException(404, 'A selected document is unavailable.')
        result.append(dict(row))
    return result
