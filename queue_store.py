"""Durable local job queue. Each operation owns its connection and transaction."""
from contextlib import contextmanager
import hashlib
import json
import sqlite3
import time
import uuid


class Conflict(ValueError):
    """The requested transition conflicts with persisted state."""


class Queue:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = str(path), clock
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, idem_key TEXT UNIQUE,
                    state TEXT NOT NULL CHECK(state IN ('queued','running','succeeded','dead')),
                    attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL,
                    available_at REAL NOT NULL, lease_until REAL, lease_token TEXT,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    result TEXT, error TEXT
                );
                CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(state, available_at, created_at);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                    event TEXT NOT NULL, at REAL NOT NULL, attempt INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_job ON events(job_id, seq);
            ''')

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA synchronous=FULL')
        try:
            if write:
                db.execute('BEGIN IMMEDIATE')
            yield db
            if write:
                db.execute('COMMIT')
        except BaseException:
            if db.in_transaction:
                db.execute('ROLLBACK')
            raise
        finally:
            db.close()

    def event(self, db, job_id, name, now, attempt):
        db.execute('INSERT INTO events(job_id,event,at,attempt) VALUES(?,?,?,?)', (job_id, name, now, attempt))

    @staticmethod
    def decode(row, include_token=False):
        if row is None:
            return None
        result = dict(row)
        result['payload'] = json.loads(result['payload'])
        result['result'] = json.loads(result['result']) if result['result'] is not None else None
        result.pop('fingerprint', None)
        result.pop('idem_key', None)
        if not include_token:
            result.pop('lease_token', None)
        return result

    def submit(self, kind, payload, key=None, max_attempts=3):
        if kind not in ('echo', 'sha256', 'fail'):
            raise ValueError('kind must be echo, sha256 or fail')
        if not isinstance(payload, dict):
            raise ValueError('payload must be an object')
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= 10:
            raise ValueError('max_attempts must be an integer from 1 to 10')
        if key is not None and (not isinstance(key, str) or not 1 <= len(key) <= 200):
            raise ValueError('idempotency key must contain 1..200 characters')
        encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False)
        fingerprint = hashlib.sha256(json.dumps([kind, encoded, max_attempts]).encode()).hexdigest()
        now, job_id = self.clock(), uuid.uuid4().hex
        with self.connection(write=True) as db:
            if key is not None:
                existing = db.execute('SELECT * FROM jobs WHERE idem_key=?', (key,)).fetchone()
                if existing:
                    if existing['fingerprint'] != fingerprint:
                        raise Conflict('Idempotency key already used for a different request')
                    return self.decode(existing), False
            db.execute('''INSERT INTO jobs(id,kind,payload,fingerprint,idem_key,state,max_attempts,
                          available_at,created_at,updated_at) VALUES(?,?,?,?,?,'queued',?,?,?,?)''',
                       (job_id, kind, encoded, fingerprint, key, max_attempts, now, now, now))
            self.event(db, job_id, 'submitted', now, 0)
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        return self.decode(row), True

    def claim(self, lease_seconds=30):
        if not 1 <= lease_seconds <= 3600:
            raise ValueError('lease_seconds must be between 1 and 3600')
        now = self.clock()
        with self.connection(write=True) as db:
            expired = db.execute("SELECT * FROM jobs WHERE state='running' AND lease_until<=?", (now,)).fetchall()
            for row in expired:
                state = 'dead' if row['attempts'] >= row['max_attempts'] else 'queued'
                db.execute('''UPDATE jobs SET state=?,lease_until=NULL,lease_token=NULL,
                           available_at=?,updated_at=?,error='Worker lease expired' WHERE id=?''',
                           (state, now, now, row['id']))
                self.event(db, row['id'], 'lease_expired' if state == 'queued' else 'dead', now, row['attempts'])
            row = db.execute("SELECT * FROM jobs WHERE state='queued' AND available_at<=? ORDER BY available_at,created_at,id LIMIT 1", (now,)).fetchone()
            if row is None:
                return None
            token = uuid.uuid4().hex
            db.execute("UPDATE jobs SET state='running',attempts=attempts+1,lease_until=?,lease_token=?,updated_at=? WHERE id=?", (now + lease_seconds, token, now, row['id']))
            self.event(db, row['id'], 'claimed', now, row['attempts'] + 1)
            return self.decode(db.execute('SELECT * FROM jobs WHERE id=?', (row['id'],)).fetchone(), True)

    def finish(self, job_id, token, result=None, error=None):
        encoded = json.dumps(result, allow_nan=False)
        now = self.clock()
        with self.connection(write=True) as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row['state'] != 'running' or row['lease_token'] != token or row['lease_until'] <= now:
                raise Conflict('Lease is stale, expired or already completed')
            state = 'succeeded' if error is None else ('dead' if row['attempts'] >= row['max_attempts'] else 'queued')
            delay = min(60, 2 ** (row['attempts'] - 1)) if state == 'queued' else 0
            db.execute('''UPDATE jobs SET state=?,result=?,error=?,available_at=?,lease_until=NULL,
                          lease_token=NULL,updated_at=? WHERE id=?''',
                       (state, encoded if error is None else None, str(error)[:1000] if error is not None else None, now + delay, now, job_id))
            self.event(db, job_id, 'retry_scheduled' if state == 'queued' else state, now, row['attempts'])
            return self.decode(db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())

    def retry(self, job_id):
        now = self.clock()
        with self.connection(write=True) as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row['state'] != 'dead':
                raise Conflict('Only dead jobs can be retried')
            db.execute("UPDATE jobs SET state='queued',attempts=0,available_at=?,updated_at=?,error=NULL WHERE id=?", (now, now, job_id))
            self.event(db, job_id, 'manual_retry', now, 0)
            return self.decode(db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())

    def get(self, job_id):
        with self.connection() as db:
            return self.decode(db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())

    def list(self, limit=100):
        with self.connection() as db:
            return [self.decode(row) for row in db.execute('SELECT * FROM jobs ORDER BY created_at DESC,id LIMIT ?', (limit,))]

    def events(self, job_id):
        with self.connection() as db:
            return [dict(row) for row in db.execute('SELECT * FROM events WHERE job_id=? ORDER BY seq', (job_id,))]

    def metrics(self):
        counts = dict.fromkeys(('queued', 'running', 'succeeded', 'dead'), 0)
        with self.connection() as db:
            counts.update({row['state']: row['n'] for row in db.execute('SELECT state,COUNT(*) AS n FROM jobs GROUP BY state')})
        return counts
