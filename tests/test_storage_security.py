import os
import stat
from pathlib import Path
from backend.db import connect

def test_existing_database_is_owner_only(tmp_path,monkeypatch):
    folder=tmp_path/'private';folder.mkdir(mode=0o755)
    path=folder/'control.db';path.touch();path.chmod(0o644)
    monkeypatch.setenv('CONTROL_DB',str(path))
    with connect() as db: db.execute('CREATE TABLE example(id INTEGER)')
    assert stat.S_IMODE(folder.stat().st_mode)==0o700
    assert stat.S_IMODE(path.stat().st_mode)==0o600

def test_new_database_ignores_permissive_umask(tmp_path,monkeypatch):
    path=tmp_path/'private'/'control.db'
    monkeypatch.setenv('CONTROL_DB',str(path))
    old=os.umask(0)
    try:
        with connect() as db: db.execute('CREATE TABLE example(id INTEGER)')
    finally: os.umask(old)
    assert stat.S_IMODE(path.parent.stat().st_mode)==0o700
    assert stat.S_IMODE(path.stat().st_mode)==0o600
