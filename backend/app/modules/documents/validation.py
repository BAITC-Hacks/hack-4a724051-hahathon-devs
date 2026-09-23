"""Проверка загруженного файла до разбора.

Тип определяем по содержимому, а не по имени и заголовку браузера. Для
xlsx/docx (это zip) проверяем число файлов, суммарный размер после распаковки,
степень сжатия и отсутствие макросов, чтобы zip-бомба не дошла до парсера.
"""

import io
import re
import zipfile
from dataclasses import dataclass

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_ZIP_ENTRIES = 2000
MAX_ZIP_UNCOMPRESSED = 60 * 1024 * 1024
MAX_ZIP_RATIO = 100

KINDS = {"xlsx", "docx", "pdf", "jpeg", "png"}


class FileRejected(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(code)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class CheckedFile:
    kind: str
    filename: str
    size: int


def safe_filename(name: str) -> str:
    name = (name or "file").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[^\w.\- ()]+", "_", name).strip(" .")
    return (name or "file")[:120]


def _check_zip(data: bytes) -> str:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise FileRejected("broken_file", "Файл повреждён.") from e
    infos = archive.infolist()
    if len(infos) > MAX_ZIP_ENTRIES:
        raise FileRejected("too_complex", "В файле слишком много частей.")
    total = 0
    for info in infos:
        if info.flag_bits & 0x1:
            raise FileRejected("encrypted", "Файл защищён паролем, откройте доступ и загрузите снова.")
        if info.filename.startswith("/") or ".." in info.filename.split("/"):
            raise FileRejected("broken_file", "Файл повреждён.")
        total += info.file_size
        if info.compress_size and info.file_size / info.compress_size > MAX_ZIP_RATIO and info.file_size > 1_000_000:
            raise FileRejected("too_large_unpacked", "Файл слишком сильно сжат, такой файл не принимаем.")
    if total > MAX_ZIP_UNCOMPRESSED:
        raise FileRejected("too_large_unpacked", "Файл после распаковки слишком большой.")
    names = {i.filename for i in infos}
    if any(n.lower().endswith("vbaproject.bin") for n in names):
        raise FileRejected("macros", "Файлы с макросами не принимаем. Сохраните как обычный .xlsx или .docx.")
    try:
        content_types = archive.read("[Content_Types].xml").decode("utf-8", "ignore")
    except KeyError as e:
        raise FileRejected("unsupported_type", "Неизвестный формат файла.") from e
    if "spreadsheetml.sheet.main" in content_types:
        return "xlsx"
    if "wordprocessingml.document.main" in content_types:
        return "docx"
    raise FileRejected("unsupported_type", "Поддерживаются Excel (.xlsx), Word (.docx), PDF, JPEG и PNG.")


def check_file(filename: str, data: bytes) -> CheckedFile:
    if not data:
        raise FileRejected("empty_file", "Файл пустой.")
    if len(data) > MAX_FILE_BYTES:
        raise FileRejected("file_too_large", "Файл больше 10 МБ.")
    if data.startswith(b"%PDF-"):
        kind = "pdf"
    elif data.startswith(b"\xff\xd8\xff"):
        kind = "jpeg"
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        kind = "png"
    elif data.startswith(b"PK\x03\x04"):
        kind = _check_zip(data)
    elif data.startswith(b"\xd0\xcf\x11\xe0"):
        raise FileRejected("legacy_office", "Старые .xls/.doc не принимаем, сохраните файл как .xlsx или .docx.")
    else:
        raise FileRejected("unsupported_type", "Поддерживаются Excel (.xlsx), Word (.docx), PDF, JPEG и PNG.")
    return CheckedFile(kind, safe_filename(filename), len(data))
