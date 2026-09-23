"""Atomic, durable daily token reservations for paid model calls."""

import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.core.errors import AppError


class BudgetExceeded(AppError):
    def __init__(self):
        super().__init__("llm_budget_exhausted", "Лимит запросов к модели исчерпан.", 503)


class CallAlreadyReserved(AppError):
    def __init__(self):
        super().__init__("llm_call_replayed", "Этот вызов модели уже был выполнен.", 409)


class SessionExpired(AppError):
    def __init__(self):
        super().__init__("session_expired", "Сессия истекла.", 401)


class BudgetUnavailable(AppError):
    def __init__(self):
        super().__init__("llm_budget_unavailable", "Учёт лимита модели недоступен.", 503)


class DailyTokenBudget:
    """Reservations remain charged unless a completed response reports usage."""

    def __init__(self, db_path: Path, *, session_limit: int, site_limit: int):
        if session_limit <= 0 or site_limit <= 0:
            raise ValueError("Token budgets must be positive")
        self.db_path = Path(db_path)
        self.session_limit = session_limit
        self.site_limit = site_limit
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS llm_token_reservations (
                    call_id TEXT PRIMARY KEY,
                    day_utc TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    charged_tokens INTEGER NOT NULL CHECK (charged_tokens >= 0),
                    state TEXT NOT NULL CHECK (state IN ('reserved', 'completed'))
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS llm_token_session_day
                ON llm_token_reservations(day_utc, session_id)
            """)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _day() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def reserve(self, *, call_id: str, session_id: str, tokens: int) -> None:
        if not call_id or len(call_id) > 128 or not session_id or len(session_id) > 128:
            raise ValueError("Invalid reservation identity")
        if tokens <= 0:
            raise ValueError("Reservation must be positive")
        day = self._day()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM llm_token_reservations WHERE call_id=?", (call_id,)).fetchone():
                raise CallAlreadyReserved()
            try:
                session = db.execute(
                    "SELECT expires_at FROM sessions WHERE id=?", (session_id,),
                ).fetchone()
            except sqlite3.OperationalError:
                # A missing or incompatible session table must never authorize a paid call.
                raise BudgetUnavailable() from None
            if session is None or session[0] <= time.time():
                raise SessionExpired()
            site_used = db.execute(
                "SELECT COALESCE(SUM(charged_tokens), 0) FROM llm_token_reservations WHERE day_utc=?",
                (day,),
            ).fetchone()[0]
            session_used = db.execute(
                "SELECT COALESCE(SUM(charged_tokens), 0) FROM llm_token_reservations "
                "WHERE day_utc=? AND session_id=?", (day, session_id),
            ).fetchone()[0]
            if site_used + tokens > self.site_limit or session_used + tokens > self.session_limit:
                raise BudgetExceeded()
            db.execute(
                "INSERT INTO llm_token_reservations VALUES (?, ?, ?, ?, 'reserved')",
                (call_id, day, session_id, tokens),
            )

    def settle(self, *, call_id: str, actual_tokens: int) -> None:
        if actual_tokens < 0:
            raise ValueError("Reported token usage cannot be negative")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT state FROM llm_token_reservations WHERE call_id=?", (call_id,),
            ).fetchone()
            if row is None or row[0] != "reserved":
                raise CallAlreadyReserved()
            db.execute(
                "UPDATE llm_token_reservations SET charged_tokens=?, state='completed' "
                "WHERE call_id=?", (actual_tokens, call_id),
            )
