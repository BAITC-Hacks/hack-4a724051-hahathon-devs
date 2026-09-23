"""Session-owned document storage and fail-closed ingestion."""
import asyncio
from contextlib import contextmanager
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from xml.etree import ElementTree
from pathlib import Path
from uuid import UUID, uuid4

from app.contracts import ParsedDocument
from app.core.errors import AppError, not_found
from app.core.privacy import payment_data
from .models import AssetView, OwnedImage
from .scanner import scan_command

MAX_FILE = 10 * 1024 * 1024
MAX_SESSION_ASSETS = 20
MAX_SESSION_BYTES = 100 * 1024 * 1024
MAX_GLOBAL_BYTES = 1024 * 1024 * 1024
MAX_ZIP_ENTRIES = 1000
MAX_ZIP_EXPANDED = 50 * 1024 * 1024
MAX_ZIP_RATIO = 100
MIME = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".txt": "text/plain",
    ".csv": "text/csv",
}


def _validate_file(name: str, content_type: str, content: bytes) -> tuple[str, str]:
    if not name or len(name) > 255 or Path(name).name != name or "\\" in name or "\x00" in name:
        raise AppError("invalid_filename", "Некорректное имя файла.", 422)
    extension = Path(name).suffix.lower()
    if extension not in MIME:
        raise AppError("unsupported_document", "Формат файла не поддерживается. XLS и DOC не поддерживаются.", 415)
    expected = MIME[extension]
    supplied = content_type.split(";", 1)[0].strip().lower()
    if supplied != expected:
        raise AppError("mime_mismatch", "Тип файла не совпадает с расширением.", 415)
    if not content or len(content) > MAX_FILE:
        raise AppError("file_size", "Размер файла должен быть от 1 байта до 10 МиБ.", 413)
    if extension in {".xlsx", ".docx"}:
        _validate_ooxml(content, extension)
    elif extension == ".pdf" and not content.startswith(b"%PDF-"):
        raise AppError("signature_mismatch", "Содержимое PDF не соответствует формату.", 415)
    elif extension in {".jpg", ".jpeg"} and not (content.startswith(b"\xff\xd8\xff") and content.endswith(b"\xff\xd9")):
        raise AppError("signature_mismatch", "Содержимое JPEG не соответствует формату.", 415)
    elif extension == ".png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise AppError("signature_mismatch", "Содержимое PNG не соответствует формату.", 415)
    elif extension in {".txt", ".csv"}:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise AppError("text_encoding", "Текстовый файл должен быть в UTF-8.", 415)
        if "\x00" in text:
            raise AppError("signature_mismatch", "Файл похож на двоичные данные.", 415)
    return extension, expected


def _validate_ooxml(content: bytes, extension: str) -> None:
    import io
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ZIP_ENTRIES:
                raise ValueError("entries")
            names = {item.filename for item in entries}
            if len(names) != len(entries):
                raise ValueError("duplicate entries")
            required = "xl/workbook.xml" if extension == ".xlsx" else "word/document.xml"
            if "[Content_Types].xml" not in names or required not in names:
                raise ValueError("structure")
            total = 0
            for item in entries:
                name = item.filename.replace("\\", "/")
                parts = name.split("/")
                if name.startswith("/") or ".." in parts or not name or item.flag_bits & 1:
                    raise ValueError("zip path or encryption")
                if (item.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("zip symlink")
                lower = name.lower()
                if ("vbaproject" in lower or "activex" in lower or "embeddings/" in lower
                        or "externallinks/" in lower or lower.endswith((".exe", ".dll", ".js", ".vbs"))):
                    raise ValueError("active content")
                total += item.file_size
                if total > MAX_ZIP_EXPANDED or item.file_size > MAX_ZIP_EXPANDED:
                    raise ValueError("expanded size")
                if item.file_size > MAX_ZIP_RATIO * max(1, item.compress_size):
                    raise ValueError("compression ratio")
                if lower.endswith(".rels"):
                    if item.file_size > 1_000_000:
                        raise ValueError("oversized relationship")
                    relation = archive.read(item, pwd=None)
                    root = ElementTree.fromstring(relation)
                    if any(element.attrib.get("TargetMode", "").lower() == "external" for element in root.iter()):
                        raise ValueError("external relationship")
    except (zipfile.BadZipFile, RuntimeError, ValueError, ElementTree.ParseError) as error:
        raise AppError("unsafe_document", "Структура документа небезопасна или повреждена.", 415) from error


class DocumentsService:
    def __init__(self, db_path: Path, root_path: Path, scanner_command: list[str] | None,
                 scanner_timeout: int = 15, parser_timeout: int = 20):
        self.db_path = Path(db_path)
        self.root_path = Path(root_path)
        self.scanner_command = tuple(scanner_command or ())
        self.scanner_timeout = scanner_timeout
        self.parser_timeout = parser_timeout

    def initialize(self) -> None:
        self.root_path.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.root_path / "quarantine").mkdir(exist_ok=True, mode=0o700)
        (self.root_path / "clean").mkdir(exist_ok=True, mode=0o700)
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS document_assets (
                id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                name TEXT NOT NULL, content_type TEXT NOT NULL, size_bytes INTEGER NOT NULL,
                status TEXT NOT NULL, warnings TEXT NOT NULL, text TEXT NOT NULL,
                extension TEXT NOT NULL, image_media_type TEXT, image_count INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS document_assets_session ON document_assets(session_id)")
            columns = {row[1] for row in db.execute("PRAGMA table_info(document_assets)")}
            if "image_count" not in columns:
                db.execute("ALTER TABLE document_assets ADD COLUMN image_count INTEGER NOT NULL DEFAULT 0")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=3)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _view(row: sqlite3.Row) -> AssetView:
        return AssetView(id=UUID(row["id"]), name=row["name"], content_type=row["content_type"],
                         size_bytes=row["size_bytes"], status=row["status"],
                         warnings=json.loads(row["warnings"]), created_at=row["created_at"])

    def _owned(self, db: sqlite3.Connection, session_id: str, asset_id: str) -> sqlite3.Row:
        if not db.execute("SELECT 1 FROM sessions WHERE id=? AND expires_at>?", (session_id, time.time())).fetchone():
            raise AppError("session_required", "Сессия истекла.", 401)
        row = db.execute("SELECT * FROM document_assets WHERE id=? AND session_id=?", (asset_id, session_id)).fetchone()
        if row is None:
            raise not_found()
        return row

    def _scan(self, path: Path) -> tuple[bool, str]:
        if not self.scanner_command:
            return False, "Антивирус не настроен; файл помещён в карантин."
        try:
            result = subprocess.run(scan_command(self.scanner_command, path), stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    timeout=self.scanner_timeout, shell=False, check=False,
                                    cwd=self.root_path / "quarantine", env=self._subprocess_env())
            if result.returncode == 0:
                return True, ""
            if result.returncode == 1:
                return False, "Антивирус обнаружил угрозу; файл помещён в карантин."
        except (OSError, subprocess.TimeoutExpired):
            pass
        return False, "Антивирус недоступен; файл помещён в карантин."

    @staticmethod
    def _subprocess_env() -> dict[str, str]:
        allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL",
                   "PROGRAMDATA", "PROGRAMFILES", "ALLUSERSPROFILE", "USERPROFILE", "LOCALAPPDATA", "APPDATA"}
        return {key: value for key, value in os.environ.items() if key.upper() in allowed}

    def _parse(self, path: Path, extension: str) -> tuple[dict, list[bytes]]:
        parser = Path(__file__).with_name("parser.py")
        with tempfile.TemporaryDirectory() as work:
            image_path = Path(work) / "normalized-image"
            stdout_path = Path(work) / "stdout"
            stderr_path = Path(work) / "stderr"
            try:
                with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
                    result = subprocess.run([sys.executable, "-I", str(parser), str(path), extension, str(image_path)],
                                            cwd=work, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                            timeout=self.parser_timeout, shell=False, check=False,
                                            env=self._subprocess_env())
            except subprocess.TimeoutExpired as error:
                raise AppError("document_parse_timeout", "Обработка файла заняла слишком много времени.", 422) from error
            if result.returncode != 0 or stdout_path.stat().st_size > 120_000:
                raise AppError("document_parse_failed", "Не удалось безопасно разобрать файл.", 422)
            try:
                parsed = json.loads(stdout_path.read_text(encoding="utf-8"))
            except (UnicodeError, ValueError) as error:
                raise AppError("document_parse_failed", "Парсер вернул некорректный результат.", 422) from error
            if not isinstance(parsed.get("text"), str) or len(parsed["text"]) > 40000 or parsed.get("status") not in {"ready", "partial"}:
                raise AppError("document_parse_failed", "Парсер вернул некорректный результат.", 422)
            image_paths = [image_path] if image_path.exists() else sorted(image_path.parent.glob("normalized-image-[0-3]"))
            images = []
            for image_file in image_paths:
                if image_file.stat().st_size > MAX_FILE:
                    raise AppError("image_too_large", "Изображение после обработки слишком большое.", 413)
                images.append(image_file.read_bytes())
            if len(images) > 4 or (images and parsed.get("image_media_type") not in {"image/jpeg", "image/png"}):
                raise AppError("document_parse_failed", "Парсер вернул некорректные изображения.", 422)
            return parsed, images

    def _upload(self, session_id: str, name: str, content_type: str, content: bytes) -> AssetView:
        extension, expected = _validate_file(name, content_type, content)
        asset_id = str(uuid4())
        quarantine = self.root_path / "quarantine" / asset_id
        clean = self.root_path / "clean" / asset_id
        now = time.time()
        # Reserve quota under a write lock before storing bytes, so concurrent uploads count.
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM sessions WHERE id=? AND expires_at>?", (session_id, now)).fetchone():
                raise AppError("session_required", "Сессия истекла.", 401)
            count, size = db.execute("SELECT count(*), coalesce(sum(size_bytes),0) FROM document_assets WHERE session_id=?", (session_id,)).fetchone()
            total = db.execute("SELECT coalesce(sum(size_bytes),0) FROM document_assets").fetchone()[0]
            if count >= MAX_SESSION_ASSETS or size + len(content) > MAX_SESSION_BYTES or total + len(content) > MAX_GLOBAL_BYTES:
                raise AppError("asset_quota", "Достигнут лимит хранения файлов.", 413)
            db.execute("""INSERT INTO document_assets
                (id,session_id,name,content_type,size_bytes,status,warnings,text,extension,image_media_type,image_count,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (asset_id, session_id, name, expected, len(content), "quarantined",
                 '["Проверка файла не завершена."]', "", extension, None, 0, now))
        try:
            with quarantine.open("xb") as file:
                os.chmod(quarantine, 0o600)
                file.write(content)
            scanned, scan_warning = self._scan(quarantine)
            parsed = {"text": "", "warnings": [scan_warning], "status": "quarantined", "image_media_type": None}
            images: list[bytes] = []
            if scanned:
                parsed, images = self._parse(quarantine, extension)
                if payment_data(parsed["text"]):
                    raise AppError("sensitive_data_rejected", "Документ содержит возможные платёжные данные или секреты.", 422)
                if images:
                    for index, image in enumerate(images):
                        image_file = clean.with_name(f"{asset_id}-{index}")
                        image_file.write_bytes(image)
                        os.chmod(image_file, 0o600)
                    quarantine.unlink(missing_ok=True)
                else:
                    quarantine.replace(clean)
            with self._db() as db:
                db.execute("""UPDATE document_assets SET status=?,warnings=?,text=?,image_media_type=?,image_count=?
                    WHERE id=? AND session_id=?""",
                    (parsed["status"], json.dumps(parsed["warnings"], ensure_ascii=False), parsed["text"],
                     parsed.get("image_media_type"), len(images), asset_id, session_id))
        except Exception:
            quarantine.unlink(missing_ok=True)
            clean.unlink(missing_ok=True)
            for index in range(4):
                clean.with_name(f"{asset_id}-{index}").unlink(missing_ok=True)
            with self._db() as db:
                db.execute("DELETE FROM document_assets WHERE id=? AND session_id=?", (asset_id, session_id))
            raise
        return AssetView(id=UUID(asset_id), name=name, content_type=expected, size_bytes=len(content),
                         status=parsed["status"], warnings=parsed["warnings"], created_at=now)

    async def upload(self, session_id: str, name: str, content_type: str, content: bytes) -> AssetView:
        return await asyncio.to_thread(self._upload, session_id, name, content_type, content)

    def _get(self, session_id: str, asset_id: str) -> AssetView:
        with self._db() as db:
            return self._view(self._owned(db, session_id, asset_id))

    async def get(self, session_id: str, asset_id: str) -> AssetView:
        return await asyncio.to_thread(self._get, session_id, asset_id)

    def _resolve(self, session_id: str, asset_ids: list[str]) -> list[ParsedDocument]:
        if len(asset_ids) > 3:
            raise AppError("too_many_assets", "В одном сообщении можно прикрепить не более трёх файлов.", 422)
        documents = []
        image_count = 0
        with self._db() as db:
            for asset_id in asset_ids:
                row = self._owned(db, session_id, asset_id)
                if row["status"] not in {"ready", "partial"}:
                    raise AppError("document_quarantined", "Файл не прошёл антивирусную проверку.", 422)
                warnings = json.loads(row["warnings"])
                if image_count + row["image_count"] > 4:
                    warnings.append("Из-за общего лимита переданы только первые четыре изображения вложений.")
                image_count += row["image_count"]
                documents.append(ParsedDocument(asset_id=UUID(asset_id), status=row["status"],
                                                text=row["text"], warnings=warnings))
        return documents

    async def resolve(self, session_id: str, asset_ids: list[str]) -> list[ParsedDocument]:
        return await asyncio.to_thread(self._resolve, session_id, asset_ids)

    def _images(self, session_id: str, asset_ids: list[str]) -> list[OwnedImage]:
        if len(asset_ids) > 3:
            raise AppError("too_many_assets", "В одном сообщении можно прикрепить не более трёх файлов.", 422)
        images = []
        with self._db() as db:
            for asset_id in asset_ids:
                row = self._owned(db, session_id, asset_id)
                if row["status"] not in {"ready", "partial"}:
                    raise AppError("document_quarantined", "Файл не прошёл антивирусную проверку.", 422)
                for index in range(min(row["image_count"], 4 - len(images))):
                    content = (self.root_path / "clean" / f"{asset_id}-{index}").read_bytes()
                    images.append(OwnedImage(UUID(asset_id), row["image_media_type"], content))
        return images

    async def images(self, session_id: str, asset_ids: list[str]) -> list[OwnedImage]:
        return await asyncio.to_thread(self._images, session_id, asset_ids)

    def _delete(self, session_id: str, asset_id: str) -> None:
        with self._db() as db:
            row = self._owned(db, session_id, asset_id)
            db.execute("DELETE FROM document_assets WHERE id=? AND session_id=?", (asset_id, session_id))
        self._remove_blobs(asset_id)

    async def delete(self, session_id: str, asset_id: str) -> None:
        await asyncio.to_thread(self._delete, session_id, asset_id)

    def _remove_blobs(self, asset_id: str) -> None:
        for folder in ("quarantine", "clean"):
            (self.root_path / folder / asset_id).unlink(missing_ok=True)
        for index in range(4):
            (self.root_path / "clean" / f"{asset_id}-{index}").unlink(missing_ok=True)

    def _cleanup(self, grace_seconds: int) -> int:
        now = time.time()
        with self._db() as db:
            rows = db.execute("""SELECT a.id FROM document_assets a LEFT JOIN sessions s ON s.id=a.session_id
                WHERE s.id IS NULL OR s.expires_at<=? OR (a.status='quarantined' AND a.created_at<?)""",
                (now, now - max(grace_seconds, 3600))).fetchall()
            ids = [row["id"] for row in rows]
            for asset_id in ids:
                db.execute("DELETE FROM document_assets WHERE id=?", (asset_id,))
            live = {row["id"] for row in db.execute("SELECT id FROM document_assets")}
        for asset_id in ids:
            self._remove_blobs(asset_id)
        for folder in ("quarantine", "clean"):
            for file in (self.root_path / folder).iterdir():
                # UUID includes hyphens: image files use a 36-character UUID prefix.
                asset_id = file.name[:36]
                if asset_id not in live and file.stat().st_mtime < now - max(grace_seconds, 3600):
                    file.unlink(missing_ok=True)
        return len(ids)

    async def cleanup(self, grace_seconds: int = 3600) -> int:
        return await asyncio.to_thread(self._cleanup, grace_seconds)
