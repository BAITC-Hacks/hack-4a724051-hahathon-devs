"""Durable local development adapter. Replace through StateStore for PostgreSQL."""
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from app.core.errors import AppError, not_found
from app.modules.chat.models import (
    AssistantOutput, ClaimedJob, ConversationView, MessageView,
    Session, TurnInput, TurnSubmission, TurnView,
)


class SQLiteStateStore:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connection(self, *, write: bool = False):
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError("Unsupported local storage schema version")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
                    expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL
                    REFERENCES sessions(id) ON DELETE CASCADE, created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS turns (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL
                    REFERENCES conversations(id) ON DELETE CASCADE,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    idem_key TEXT NOT NULL, payload_hash TEXT NOT NULL, payload TEXT NOT NULL,
                    status TEXT NOT NULL, output TEXT, error_code TEXT,
                    created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    UNIQUE(session_id, idem_key)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL
                    REFERENCES conversations(id) ON DELETE CASCADE,
                    turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
                    role TEXT NOT NULL, text TEXT NOT NULL, created_at REAL NOT NULL,
                    UNIQUE(turn_id, role)
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    turn_id TEXT PRIMARY KEY REFERENCES turns(id) ON DELETE CASCADE,
                    status TEXT NOT NULL, lease_token TEXT, lease_until REAL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rate_buckets (
                    bucket TEXT NOT NULL, window INTEGER NOT NULL, count INTEGER NOT NULL,
                    PRIMARY KEY(bucket, window)
                );
                CREATE INDEX IF NOT EXISTS turn_conversation ON turns(conversation_id, status);
                CREATE INDEX IF NOT EXISTS job_queue ON jobs(status, created_at);
                CREATE INDEX IF NOT EXISTS message_conversation ON messages(conversation_id, created_at);
                PRAGMA user_version=1;
            """)

    def ping(self) -> None:
        with self.connection() as db:
            db.execute("SELECT 1 FROM sessions LIMIT 1").fetchone()

    def consume_rate(self, bucket: str, limit: int, now: float) -> None:
        window = int(now // 60)
        with self.connection(write=True) as db:
            db.execute("DELETE FROM rate_buckets WHERE window < ?", (window - 1,))
            row = db.execute(
                "SELECT count FROM rate_buckets WHERE bucket=? AND window=?", (bucket, window)
            ).fetchone()
            if row and row["count"] >= limit:
                raise AppError("rate_limited", "Слишком много запросов. Повторите позже.", 429, True)
            db.execute(
                "INSERT INTO rate_buckets VALUES (?, ?, 1) "
                "ON CONFLICT(bucket, window) DO UPDATE SET count=count+1", (bucket, window)
            )

    def create_session(self, token_hash: str, expires_at: float) -> Session:
        session = Session(str(uuid4()), expires_at)
        with self.connection(write=True) as db:
            db.execute("INSERT INTO sessions VALUES (?, ?, ?)", (session.id, token_hash, expires_at))
        return session

    def find_session(self, token_hash: str, now: float) -> Session | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM sessions WHERE token_hash=? AND expires_at>?", (token_hash, now)
            ).fetchone()
            return Session(row["id"], row["expires_at"]) if row else None

    def delete_session(self, session_id: str) -> None:
        # ON DELETE CASCADE also removes conversations, turns, messages, jobs,
        # uploaded assets and the demo cart that belong to this session.
        with self.connection(write=True) as db:
            db.execute("DELETE FROM sessions WHERE id=?", (session_id,))

    def session_alive(self, session_id: str, now: float) -> bool:
        with self.connection() as db:
            return bool(db.execute(
                "SELECT 1 FROM sessions WHERE id=? AND expires_at>?", (session_id, now)
            ).fetchone())

    @staticmethod
    def _owned(db, session_id: str, conversation_id: str) -> None:
        if not db.execute(
            "SELECT 1 FROM conversations WHERE id=? AND session_id=?",
            (conversation_id, session_id),
        ).fetchone():
            raise not_found()

    def conversation_owned(self, session_id: str, conversation_id: str) -> None:
        with self.connection() as db:
            self._owned(db, session_id, conversation_id)

    def create_conversation(self, session_id: str, now: float, limit: int) -> ConversationView:
        conversation = ConversationView(id=uuid4(), created_at=now)
        with self.connection(write=True) as db:
            count = db.execute(
                "SELECT count(*) FROM conversations WHERE session_id=?", (session_id,)
            ).fetchone()[0]
            if count >= limit:
                raise AppError("conversation_limit", "Достигнут лимит диалогов сессии.", 429)
            db.execute("INSERT INTO conversations VALUES (?, ?, ?)",
                       (str(conversation.id), session_id, now))
        return conversation

    def messages(self, session_id: str, conversation_id: str, limit: int) -> list[MessageView]:
        with self.connection() as db:
            self._owned(db, session_id, conversation_id)
            rows = db.execute(
                "SELECT * FROM messages WHERE conversation_id=? "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?", (conversation_id, min(limit, 50))
            ).fetchall()
        return [MessageView(id=r["id"], role=r["role"], text=r["text"], created_at=r["created_at"])
                for r in reversed(rows)]

    @staticmethod
    def _view(row) -> TurnView:
        return TurnView(
            id=row["id"], conversation_id=row["conversation_id"], status=row["status"],
            output=AssistantOutput.model_validate_json(row["output"]) if row["output"] else None,
            error_code=row["error_code"], created_at=row["created_at"],
        )

    def turns(self, session_id: str, conversation_id: str, limit: int = 50) -> list[TurnView]:
        with self.connection() as db:
            self._owned(db, session_id, conversation_id)
            rows = db.execute("SELECT * FROM turns WHERE conversation_id=? ORDER BY created_at DESC,rowid DESC LIMIT ?",
                              (conversation_id, min(max(limit, 1), 50))).fetchall()
        return [self._view(row) for row in reversed(rows)]

    @staticmethod
    def _payload_hash(conversation_id: str, payload: TurnInput) -> str:
        canonical = json.dumps(
            {"conversation_id": conversation_id, "body": payload.model_dump(mode="json")},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def replay(self, session_id: str, conversation_id: str, key: str,
               payload: TurnInput) -> TurnSubmission | None:
        with self.connection() as db:
            self._owned(db, session_id, conversation_id)
            prior = db.execute("SELECT * FROM turns WHERE session_id=? AND idem_key=?",
                               (session_id, key)).fetchone()
            if prior is None:
                return None
            if prior["payload_hash"] != self._payload_hash(conversation_id, payload):
                raise AppError("idempotency_conflict", "Ключ уже использован с другим запросом.", 409)
            return TurnSubmission(turn=self._view(prior), created=False)

    def submit(self, session_id: str, conversation_id: str, key: str, payload: TurnInput,
               now: float, max_pending: int, max_turns: int) -> TurnSubmission:
        raw = payload.model_dump_json()
        digest = self._payload_hash(conversation_id, payload)
        with self.connection(write=True) as db:
            self._owned(db, session_id, conversation_id)
            if not db.execute("SELECT 1 FROM sessions WHERE id=? AND expires_at>?",
                              (session_id, now)).fetchone():
                raise AppError("session_required", "Сессия истекла.", 401)
            prior = db.execute(
                "SELECT * FROM turns WHERE session_id=? AND idem_key=?", (session_id, key)
            ).fetchone()
            if prior:
                if prior["payload_hash"] != digest:
                    raise AppError("idempotency_conflict", "Ключ уже использован с другим запросом.", 409)
                return TurnSubmission(turn=self._view(prior), created=False)
            if db.execute(
                "SELECT 1 FROM turns WHERE conversation_id=? AND status IN ('queued','running')",
                (conversation_id,),
            ).fetchone():
                raise AppError("conversation_busy", "Дождитесь завершения текущего ответа.", 409, True)
            if db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0] >= max_pending:
                raise AppError("queue_full", "Очередь заполнена. Повторите позже.", 503, True)
            if db.execute("SELECT count(*) FROM turns WHERE session_id=?", (session_id,)).fetchone()[0] >= max_turns:
                raise AppError("session_turn_limit", "Достигнут лимит сообщений сессии.", 429)
            turn_id = str(uuid4())
            db.execute(
                "INSERT INTO turns VALUES (?, ?, ?, ?, ?, ?, 'queued', NULL, NULL, ?, ?)",
                (turn_id, conversation_id, session_id, key, digest, raw, now, now),
            )
            db.execute("INSERT INTO messages VALUES (?, ?, ?, 'user', ?, ?)",
                       (str(uuid4()), conversation_id, turn_id, payload.text, now))
            db.execute("INSERT INTO jobs VALUES (?, 'queued', NULL, NULL, ?)", (turn_id, now))
            row = db.execute("SELECT * FROM turns WHERE id=?", (turn_id,)).fetchone()
            return TurnSubmission(turn=self._view(row), created=True)

    def get_turn(self, session_id: str, turn_id: str) -> TurnView:
        with self.connection() as db:
            row = db.execute("SELECT * FROM turns WHERE id=? AND session_id=?",
                             (turn_id, session_id)).fetchone()
            if not row:
                raise not_found()
            return self._view(row)

    def cancel(self, session_id: str, turn_id: str, now: float) -> TurnView:
        with self.connection(write=True) as db:
            row = db.execute("SELECT * FROM turns WHERE id=? AND session_id=?",
                             (turn_id, session_id)).fetchone()
            if not row:
                raise not_found()
            if row["status"] in {"queued", "running"}:
                db.execute("UPDATE turns SET status='cancelled', updated_at=? WHERE id=?", (now, turn_id))
                db.execute("UPDATE jobs SET status='cancelled', lease_token=NULL WHERE turn_id=?", (turn_id,))
            return self._view(db.execute("SELECT * FROM turns WHERE id=?", (turn_id,)).fetchone())

    def claim(self, now: float, lease_seconds: int) -> ClaimedJob | None:
        with self.connection(write=True) as db:
            # Never automatically replay an interrupted invocation: its external outcome may be unknown.
            expired = db.execute(
                "SELECT turn_id FROM jobs WHERE status='running' AND lease_until<=?", (now,)
            ).fetchall()
            for row in expired:
                db.execute("UPDATE turns SET status='failed', error_code='execution_interrupted', "
                           "updated_at=? WHERE id=? AND status='running'", (now, row[0]))
                db.execute("UPDATE jobs SET status='failed', lease_token=NULL WHERE turn_id=?", (row[0],))
            row = db.execute(
                "SELECT t.* FROM jobs j JOIN turns t ON t.id=j.turn_id "
                "JOIN sessions s ON s.id=t.session_id "
                "WHERE j.status='queued' AND t.status='queued' AND s.expires_at>? "
                "ORDER BY j.created_at, j.turn_id LIMIT 1", (now,),
            ).fetchone()
            if not row:
                return None
            token = str(uuid4())
            db.execute("UPDATE jobs SET status='running', lease_token=?, lease_until=? WHERE turn_id=?",
                       (token, now + lease_seconds, row["id"]))
            db.execute("UPDATE turns SET status='running', updated_at=? WHERE id=?", (now, row["id"]))
            return ClaimedJob(row["id"], row["session_id"], row["conversation_id"], token,
                              TurnInput.model_validate_json(row["payload"]))

    @staticmethod
    def _active(db, job: ClaimedJob, now: float) -> bool:
        return bool(db.execute(
            "SELECT 1 FROM jobs j JOIN turns t ON t.id=j.turn_id JOIN sessions s ON s.id=t.session_id "
            "WHERE j.turn_id=? AND j.lease_token=? AND j.status='running' AND t.status='running' "
            "AND j.lease_until>? AND s.expires_at>?",
            (job.turn_id, job.lease_token, now, now),
        ).fetchone())

    def active(self, job: ClaimedJob, now: float) -> bool:
        with self.connection() as db:
            return self._active(db, job, now)

    def finish(self, job: ClaimedJob, output: AssistantOutput | None,
               error_code: str | None, now: float) -> bool:
        if (output is None) == (error_code is None):
            raise ValueError("Exactly one of output and error_code is required")
        with self.connection(write=True) as db:
            if not self._active(db, job, now):
                return False
            status = "completed" if output is not None else "failed"
            db.execute("UPDATE turns SET status=?, output=?, error_code=?, updated_at=? WHERE id=?",
                       (status, output.model_dump_json() if output else None, error_code, now, job.turn_id))
            db.execute("UPDATE jobs SET status=?, lease_token=NULL WHERE turn_id=?", (status, job.turn_id))
            if output is not None:
                db.execute("INSERT INTO messages VALUES (?, ?, ?, 'assistant', ?, ?)",
                           (str(uuid4()), job.conversation_id, job.turn_id, output.message, now))
            return True

    def cleanup(self, now: float) -> None:
        with self.connection(write=True) as db:
            db.execute("DELETE FROM sessions WHERE expires_at<=?", (now,))
            db.execute("DELETE FROM rate_buckets WHERE window<?", (int(now // 60) - 1,))
