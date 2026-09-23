# База каталога

`ekt_catalog.sql` это готовый дамп PostgreSQL с каталогом: 281 товар, таблицы `products`, `catalog_sync_runs`, `assets` (пустая) и отметки применённых миграций. Те же данные, что в `data/synthetic/ekt_products.json`.

Базу для работы приложения не обязательно поднимать вручную. Есть два режима, выберите один.

## Вариант 1. Без PostgreSQL (проще всего)

В корневом `.env`:

```dotenv
INTEGRATION_MODE=synthetic
```

Приложение само создаст SQLite-базу `backend/var/participant2.sqlite3` и загрузит в неё каталог из `data/synthetic/ekt_products.json`. Ничего ставить не нужно.

## Вариант 2. PostgreSQL с этим дампом

Нужен PostgreSQL 14 или новее.

```bash
createdb ekt
psql -v ON_ERROR_STOP=1 -d ekt -f data/db/ekt_catalog.sql
```

Windows (PowerShell), если `createdb` и `psql` не в PATH, укажите полный путь, например `"C:/Program Files/PostgreSQL/16/bin/psql.exe"`. Логин и пароль добавьте ключами `-U postgres` (пароль спросит сама программа).

В корневом `.env`:

```dotenv
INTEGRATION_MODE=catalog_db
DATABASE_URL=postgresql://postgres:ВАШ_ПАРОЛЬ@127.0.0.1:5432/ekt
```

Шаг с дампом можно пропустить: если база `ekt` пустая, приложение при старте само создаст таблицы и загрузит тот же каталог.

## После смены настроек

1. Остановите API и worker.
2. Запустите их снова (оба читают один `.env`).
3. Проверьте: http://127.0.0.1:8000/api/v1/capabilities должно показывать `"catalog": "synthetic_demo"`, а не `requires_integration`.
4. Обновите страницу интерфейса.

Если в интерфейсе написано «Каталог пока не подключён», значит бэкенд запущен с `INTEGRATION_MODE=unavailable` или не перезапускался после правки `.env`. Переменные окружения терминала важнее `.env`: если там задан `INTEGRATION_MODE`, уберите его или выставьте нужное значение.

## Пересоздать дамп

```bash
dropdb --if-exists ekt_dump && createdb ekt_dump
cd backend
INTEGRATION_MODE=catalog_db DATABASE_URL=postgresql:///ekt_dump .venv/bin/python -c "from app.bootstrap import build_container; from app.core.config import Settings; build_container(Settings())"
cd ..
pg_dump --no-owner --no-privileges --no-comments -d ekt_dump -f data/db/ekt_catalog.sql
sed -i.bak '/^\\restrict /d; /^\\unrestrict /d' data/db/ekt_catalog.sql && rm data/db/ekt_catalog.sql.bak
```
