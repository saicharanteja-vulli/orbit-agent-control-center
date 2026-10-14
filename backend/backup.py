"""Offline backup/restore. Stop API and worker before using this command."""
import argparse
import os
from pathlib import Path
import sqlite3
import sys
import tarfile
import tempfile


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['backup','restore']);args=parser.parse_args()
    db=Path(os.environ.get('CONTROL_DB','data/control.db'));root=db.parent
    if args.action=='backup':
        if not db.is_file():raise SystemExit('No database found.')
        with tempfile.TemporaryDirectory() as temp:
            snap=Path(temp)/'control.db'
            with sqlite3.connect(db) as source,sqlite3.connect(snap) as dest:source.backup(dest)
            with tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz') as archive:
                archive.add(snap,arcname='control.db')
                artifacts=root/'artifacts'
                if artifacts.exists():
                    for file in artifacts.rglob('*'):
                        if file.is_file() and not file.is_symlink():archive.add(file,arcname=str(file.relative_to(root)),recursive=False)
    else:
        if db.exists() or (root/'artifacts').exists():raise SystemExit('Restore requires an empty data volume. Keep the existing volume for rollback.')
        root.mkdir(parents=True,exist_ok=True)
        with tarfile.open(fileobj=sys.stdin.buffer,mode='r|gz') as archive:
            for item in archive:
                path=Path(item.name)
                if not item.isfile() or path.is_absolute() or '..' in path.parts or not (item.name=='control.db' or path.parts[0]=='artifacts'):
                    raise SystemExit('Unsafe archive entry.')
                archive.extract(item,path=root,filter='data')
        with sqlite3.connect(db) as conn:
            if conn.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise SystemExit('Database integrity check failed.')
            conn.execute('DELETE FROM sessions')
        print('Restored. All sessions revoked.',file=sys.stderr)

if __name__=='__main__':main()
