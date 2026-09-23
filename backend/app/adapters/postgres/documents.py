"""Файлы клиентов: загрузка, проверка, разбор и выдача ассистенту.

upload() делает всё по шагам: проверка типа и размера, антивирус, сохранение
исходника в приватную папку, разбор в отдельном процессе, запись в БД.
resolve() реализует DocumentsPort: отдаёт только файлы этой сессии, только
разобранные, в том же порядке, что запросили (включая повторы).
"""

import asyncio
import hashlib
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from psycopg_pool import ConnectionPool

from app.contracts import ParsedDocument
from app.core.errors import AppError
from app.modules.documents.extraction import extract
from app.modules.documents.scanner import NoScanner, Scanner
from app.modules.documents.validation import FileRejected, check_file

ASSET_TTL = timedelta(hours=24)
MAX_ASSETS_PER_SESSION = 20

REJECT_STATUS = {
    "file_too_large": 413, "unsupported_type": 415, "legacy_office": 415, "macros": 415,
    "scanner_unavailable": 503, "infected": 422, "too_many_files": 429,
}


@dataclass(frozen=True)
class AssetStatus:
    id: str
    filename: str
    kind: str
    status: str  # ready | partial | failed
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


class LocalBlobStore:
    """Исходники файлов на приватном диске, имя файла не от клиента."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def put(self, data: bytes) -> str:
        key = uuid.uuid4().hex
        path = self.root / key
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return key

    def delete(self, key: str) -> None:
        (self.root / key).unlink(missing_ok=True)


class PostgresDocuments:
    def __init__(self, pool: ConnectionPool, blobs: LocalBlobStore, scanner: Scanner | None = None,
                 allow_unscanned: bool = False):
        self.pool = pool
        self.blobs = blobs
        self.scanner = scanner or NoScanner()
        self.allow_unscanned = allow_unscanned

    @staticmethod
    def _reject(code: str, message: str) -> AppError:
        return AppError(code, message, REJECT_STATUS.get(code, 422))

    def upload(self, session_id: str, filename: str, data: bytes) -> AssetStatus:
        try:
            checked = check_file(filename, data)
        except FileRejected as e:
            raise self._reject(e.code, e.message) from e
        with self.pool.connection() as conn:
            active = conn.execute("SELECT count(*) FROM assets WHERE session_id = %s AND expires_at > now()",
                                  (session_id,)).fetchone()[0]
        if active >= MAX_ASSETS_PER_SESSION:
            raise self._reject("too_many_files", "Слишком много файлов в этой сессии.")

        verdict = self.scanner.scan(data)
        if verdict == "infected":
            raise self._reject("infected", "Файл не прошёл антивирусную проверку.")
        warnings = []
        if verdict == "unavailable":
            if not self.allow_unscanned:
                raise self._reject("scanner_unavailable", "Проверка файлов временно недоступна.")
            warnings.append("Файл не проверен антивирусом (демо-режим).")

        result = extract(checked.kind, data)
        key = self.blobs.put(data)
        asset_id = uuid.uuid4()
        try:
            with self.pool.connection() as conn:
                conn.execute(
                    "INSERT INTO assets (id, session_id, filename, kind, size_bytes, sha256, storage_key, "
                    "scan_status, status, error, text, warnings, expires_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (asset_id, session_id, checked.filename, checked.kind, checked.size,
                     hashlib.sha256(data).hexdigest(), key, "clean" if verdict == "clean" else "skipped_demo",
                     result.status, result.error, result.text, warnings + result.warnings,
                     datetime.now(timezone.utc) + ASSET_TTL),
                )
        except Exception:
            self.blobs.delete(key)
            raise
        return AssetStatus(str(asset_id), checked.filename, checked.kind, result.status,
                           warnings + result.warnings, result.error)

    def status(self, session_id: str, asset_id: str) -> AssetStatus:
        row = self._rows(session_id, [asset_id]).get(asset_id)
        if row is None:
            raise AppError("asset_not_found", "Файл не найден.", 404)
        return AssetStatus(asset_id, row["filename"], row["kind"], row["status"], row["warnings"], row["error"])

    def _rows(self, session_id: str, asset_ids: list[str]) -> dict[str, dict]:
        ids = []
        for value in asset_ids:
            try:
                ids.append(uuid.UUID(str(value)))
            except ValueError:
                continue
        if not ids:
            return {}
        with self.pool.connection() as conn:
            cursor = conn.execute(
                "SELECT id, filename, kind, status, error, text, warnings, scan_status FROM assets "
                "WHERE id = ANY(%s) AND session_id = %s AND expires_at > now()", (ids, session_id))
            names = [d.name for d in cursor.description]
            return {str(r[0]): dict(zip(names, r)) for r in cursor.fetchall()}

    def _resolve(self, session_id: str, asset_ids: list[str]) -> list[ParsedDocument]:
        rows = self._rows(session_id, asset_ids)
        documents = []
        for asset_id in asset_ids:
            row = rows.get(str(asset_id))
            if row is None:
                # Чужой, истёкший или несуществующий файл: отказ целиком, без пропусков.
                raise AppError("asset_not_found", "Файл не найден.", 404)
            if row["status"] not in ("ready", "partial"):
                raise AppError("asset_not_ready", f"Файл «{row['filename']}» не удалось прочитать.", 409)
            warnings = list(row["warnings"])
            if row["scan_status"] != "clean":
                warnings.append("not_virus_scanned")
            documents.append(ParsedDocument(asset_id=row["id"], status=row["status"],
                                            text=row["text"], warnings=warnings))
        return documents

    async def resolve(self, session_id: str, asset_ids: list[str]) -> list[ParsedDocument]:
        return await asyncio.to_thread(self._resolve, session_id, list(asset_ids))

    def cleanup_expired(self) -> int:
        with self.pool.connection() as conn:
            keys = [r[0] for r in conn.execute(
                "DELETE FROM assets WHERE expires_at <= now() RETURNING storage_key").fetchall()]
        for key in keys:
            self.blobs.delete(key)
        return len(keys)
