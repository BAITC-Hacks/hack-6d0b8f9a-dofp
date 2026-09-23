"""Local analyst work, stored separately from immutable analysis artifacts."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .imports import local_request


class ReviewUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: Literal['not_started', 'in_progress', 'checked']
    note: str = Field(max_length=4000)
    version: int = Field(ge=0, strict=True)


class ReviewStore:
    def __init__(self, path):
        self.path = Path(path)

    def get(self, run, gid):
        default = {'status': 'not_started', 'note': '', 'version': 0, 'updated_at': None}
        if not self.path.exists():
            return default
        with closing(sqlite3.connect(self.path, timeout=3)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='reviews'").fetchone():
                return default
            row = db.execute('SELECT status,note,version,updated_at FROM reviews WHERE run_id=? AND gid=?', (run,gid)).fetchone()
        return dict(zip(default, row)) if row else default

    def save(self, run, gid, update):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=3)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS reviews (run_id TEXT NOT NULL, gid TEXT NOT NULL, status TEXT NOT NULL, note TEXT NOT NULL, version INTEGER NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(run_id,gid))')
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT version FROM reviews WHERE run_id=? AND gid=?', (run,gid)).fetchone()
            if update.version != (row[0] if row else 0):
                raise HTTPException(409, 'Заметку уже изменили в другом окне. Обновите сохранённую версию; ваш черновик оставлен на экране.')
            result = {'status': update.status, 'note': update.note, 'version': update.version+1,
                      'updated_at': datetime.now(timezone.utc).isoformat()}
            db.execute('INSERT INTO reviews VALUES (?,?,?,?,?,?) ON CONFLICT(run_id,gid) DO UPDATE SET status=excluded.status,note=excluded.note,version=excluded.version,updated_at=excluded.updated_at',
                       (run,gid,*result.values()))
        return result


def review_router(path, current, require_node):
    store = ReviewStore(path)
    router = APIRouter()

    @router.get('/api/v1/runs/{run_id}/nodes/{gid}/review')
    def read_review(run_id: str, gid: str):
        svc = current(run_id)
        normalized = require_node(svc, gid)
        try:
            return svc.envelope(store.get(run_id, normalized))
        except sqlite3.Error as exc:
            raise HTTPException(503, 'Не удалось прочитать заметку. Повторите попытку.') from exc

    @router.put('/api/v1/runs/{run_id}/nodes/{gid}/review')
    def write_review(run_id: str, gid: str, update: ReviewUpdate, request: Request):
        local_request(request)
        svc = current(run_id)
        normalized = require_node(svc, gid)
        try:
            return svc.envelope(store.save(run_id, normalized, update))
        except (sqlite3.Error, OSError) as exc:
            raise HTTPException(503, 'Не удалось сохранить заметку. Текст остался на экране, повторите попытку.') from exc

    return router
