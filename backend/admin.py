"""Server-console-only administrator initialization and password recovery."""
import argparse
import getpass
from uuid import uuid4
from .db import init, connect
from .auth import Credentials, password_hash


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['create','reset-password'])
    parser.add_argument('username')
    args=parser.parse_args()
    password=getpass.getpass('Password (12+ characters): ')
    if password!=getpass.getpass('Confirm password: '):raise SystemExit('Passwords differ.')
    body=Credentials(username=args.username,password=password)
    init()
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        username=body.username.lower()
        if args.action=='create':
            uid=str(uuid4())
            db.execute('INSERT INTO users VALUES(?,?,?,?,1)',(uid,username,password_hash(body.password),'admin'))
            if db.execute('SELECT COUNT(*) FROM users').fetchone()[0]==1:
                db.execute('UPDATE runs SET owner_id=? WHERE owner_id IS NULL',(uid,))
        else:
            row=db.execute('SELECT id FROM users WHERE username=?',(username,)).fetchone()
            if not row:raise SystemExit('Account not found.')
            db.execute('UPDATE users SET password_hash=? WHERE id=?',(password_hash(body.password),row['id']))
            db.execute('DELETE FROM sessions WHERE user_id=?',(row['id'],))
    print('Account updated; no password was stored in command history.')

if __name__=='__main__':main()
