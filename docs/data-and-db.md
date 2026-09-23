# Данные, БД и файлы (участник 1)

## Каталог в PostgreSQL

Нужен PostgreSQL 14+ и `DATABASE_URL` в корневом `.env`, например `postgresql:///ekt`.

```
cd backend
python -m app.catalog_cli migrate            # таблицы products, catalog_sync_runs, assets
python -m app.catalog_cli import-synthetic   # 65 синтетических товаров для демо
python -m app.catalog_cli import-ekt --pages 3   # реальный API, нужны EKT_API_USER/PASSWORD
python -m app.catalog_cli status             # сколько товаров и полон ли последний импорт
```

Реальные карточки ekt.kz грузятся только в локальную БД и в репозиторий не попадают.

- В `products.raw` лежит карточка источника как есть, `Product` строится из неё через `product_from_source()`. Колонки рядом нужны для поиска: полнотекстовый по-русски и триграммы по названию.
- Импорт из API ставит `complete`, только если дошёл до пустой страницы. Лимит страниц, повтор страницы или ошибка источника дают `partial`, уже загруженное остаётся. Отсутствие товара в неполном каталоге не значит, что его нет в магазине.
- Старый снимок не перезаписывает более новый (`fetched_at`).

## Режим приложения

`INTEGRATION_MODE=catalog_db` собирает в `bootstrap.py`:

- `CatalogPort` из PostgreSQL. Пока в БД есть синтетические товары, ответы помечены `synthetic_demo_data`;
- `DocumentsPort` из PostgreSQL (таблица `assets`);
- корзину пока как раньше: демо в SQLite рядом с сессиями.

`EKT_LIVE_REFRESH=true` перечитывает цену и остаток из EKT перед предложением и подтверждением. Если EKT не ответил, остаток `unknown` и добавить в корзину нельзя, нулём он не становится.

## Файлы клиента

Проверка и разбор готовы, HTTP-ручки загрузки ещё нет. Для ручки `POST /api/v1/assets` (участник 2):

```python
status = container.documents.upload(session_id, filename, data)   # sync, вызывать в threadpool
status = container.documents.status(session_id, asset_id)
# AssetStatus(id, filename, kind, status="ready|partial|failed", warnings, error)
```

Ошибки приходят как `AppError`: 413 размер, 415 формат (в том числе .xls/.doc и файлы с макросами), 422 вирус, 429 лимит файлов на сессию, 503 сканер недоступен. Ассистент получает текст через `DocumentsPort.resolve()`: только файлы своей сессии, только разобранные, в запрошенном порядке.

- Тип определяется по содержимому. Для xlsx/docx проверяются zip-бомбы, шифрование и макросы.
- Разбор идёт в отдельном процессе с таймаутом 30 с. Лимиты: 10 МБ, 20 страниц PDF, 200 строк таблиц, 40 000 символов. Превышение даёт `partial` и предупреждение.
- Антивирус: `CLAMD_SOCKET` (clamd, протокол INSTREAM). Без сканера разбор запрещён. `DOCUMENTS_ALLOW_UNSCANNED=true` только для локального демо, в production запрещено.
- Фото и сканы PDF без текстового слоя дают `failed / needs_ocr`: OCR пока не подключён.
- Файлы и текст живут 24 часа, `documents.cleanup_expired()` удаляет записи и исходники.

## Тесты на PostgreSQL

```
createdb ekt_test
TEST_DATABASE_URL=postgresql:///ekt_test python -m pytest
```

Без `TEST_DATABASE_URL` эти тесты пропускаются. Каждый тест пересоздаёт схему `public` в тестовой БД.

## Что ещё не сделано

- Корзина, предложения и состояние чата (сессии, turns, jobs) в PostgreSQL. Сейчас они в SQLite, потому что корзина проверяет сессию в той же транзакции. Переносить их нужно вместе.
- HTTP-ручка загрузки файлов.
- OCR для фото и сканов.
- Реальная корзина ekt.kz: нет API от партнёра.
