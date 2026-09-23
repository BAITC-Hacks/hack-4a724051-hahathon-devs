"""Распознавание текста на фото и сканах через модель, понимающую изображения (OpenAI или NVIDIA).

Вызывается только когда клиент разрешил внешнюю обработку файлов. Модель просим
переписать текст как есть: артикулы и количества потом ищутся в каталоге обычным
поиском, поэтому важна точность строк, а не пересказ.
"""

import asyncio
import re
from dataclasses import dataclass

MAX_TEXT_CHARS = 40000

PROMPT = (
    "Перепиши весь текст с изображения точно как он написан: артикулы, названия, количества, единицы, цены. "
    "Каждую строку таблицы выведи отдельной строкой, ячейки раздели символом |. "
    "Ничего не добавляй, не объясняй и не исправляй. Язык не меняй (русский, казахский, английский). "
    "Если текста нет, ответь пустой строкой."
)


@dataclass(frozen=True)
class OcrResult:
    text: str
    pages_read: int
    pages_failed: int


def clean(text: str) -> str:
    text = re.sub(r"^```[a-zA-Z]*\s*|```\s*$", "", text.strip())
    lines = [line.rstrip() for line in text.splitlines()]
    return "\n".join(lines).strip()[:MAX_TEXT_CHARS]


class ImageReader:
    def __init__(self, client, model: str):
        self.client = client
        self.model = model

    def read(self, images: list[tuple[str, bytes]], session_id: str | None = None) -> OcrResult:
        from app.adapters.ai_services.client import AIBudgetExceeded, AIServiceError
        parts, failed = [], 0
        for number, (media_type, content) in enumerate(images, 1):
            try:
                text = clean(self.client.read_image(content, media_type, self.model, PROMPT, session_id=session_id))
            except AIBudgetExceeded:
                failed += len(images) - number + 1
                break
            except AIServiceError:
                failed += 1
                continue
            if text:
                parts.append(text if len(images) == 1 else f"# Страница {number}\n{text}")
        return OcrResult("\n".join(parts)[:MAX_TEXT_CHARS], len(parts), failed)

    async def aread(self, images: list[tuple[str, bytes]], session_id: str | None = None) -> OcrResult:
        return await asyncio.to_thread(self.read, images, session_id)
