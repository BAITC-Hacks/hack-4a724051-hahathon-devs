# Единая интеграция: API, ассистент, Next.js и данные

Обновление: OpenAI-планировщик, файловые маршруты, сертификаты и текстовое подтверждение подключены к HTTP runtime. Актуальные настройки/ограничения: [assistant-testing.md](assistant-testing.md).

HTTP-контракт: [openapi.json](openapi.json), версия 0.1.0. Целевая архитектура: [architecture.md](architecture.md). Внешний EKT API: [api.md](api.md). Доменные Python-интерфейсы участника 1: [contracts.md](contracts.md).

## Разделение ответственности

Участник 2 (пользователь) владеет всем Next.js/TypeScript frontend, дальнейшим развитием ИИ-ассистента и серверным слоем взаимодействия: HTTP API, сессии, диалоги, защищённое подтверждение, запуск обработки и общие контракты.

Участник 1 предоставляет каталог, поиск, правила аналогов, данные/парсинг, основную БД и бизнес-операции. В main уже пришли реализации доменной логики ассистента, поиска и корзины; они сохраняются и подключаются адаптерами, а не переписываются под HTTP DTO.

| Слой | Контракт | Ответственность |
| --- | --- | --- |
| Frontend | OpenAPI и типизированный API-клиент | Отображение, cookie-сессия, явное подтверждение |
| HTTP | `app/contracts.py`, `modules/chat/models.py` | Закрытые DTO, CSRF/Origin, ownership, безопасные ошибки |
| Chat application | `modules/chat/application.py` | Идемпотентный turn, проверка файлов, durable queue |
| Assistant/domain | `modules/assistant/*`, `modules/chat/service.py` | Работа с проверенными данными и разрешёнными инструментами |
| Интеграционные фасады | `app/integrations/ports.py` | CatalogPort, DocumentsPort, ActionsPort |
| Сохранение состояния | `modules/chat/ports.py` | StateStore с транзакционными гарантиями |
| Конкретные реализации | `app/adapters/*`, `bootstrap.py` | Подключение SQL, каталога, корзины и будущих провайдеров |

Названия похожих структур не означают одинаковый формат: доменный Product/ChatReply преобразуется в HTTP Product/AssistantOutput отдельным адаптером. Frontend не должен напрямую зависеть от dataclass доменного модуля.

## Что работает и какие ограничения сохраняются

Реализованы FastAPI factory, сессии, CSRF/Origin, лимиты запросов и тела, разговоры, история, идемпотентная отправка, отмена, опрос результата, отдельный worker и локальное durable хранилище. Задачи не исчезают при перезапуске API. Два worker не должны обработать один queued job одновременно; поздний результат не перезаписывает отмену.

По умолчанию интеграции выключены. Режим `INTEGRATION_MODE=synthetic` предназначен для явной демонстрации синтетического каталога и собственной корзины. Это не вызовы реального API ekt.kz. Все процессы должны работать с одинаковым режимом и хранилищем.

OpenAI выключен по умолчанию и не активируется наличием ключа: нужны LLM_ENABLED, принятая политика данных и ненулевые квоты. Модель планирует поиск, сервер выводит факты. AnthropicProvider сохранён для совместимости, но не подключён к HTTP runtime. Файловый сервис и парсеры реализованы; оператор должен настроить настоящий scanner. PostgreSQL-адаптер ещё требует интеграции.

`APP_ENV=production` пока блокируется. SQLite — сменный адаптер разработки. Проверки SQLite не доказывают транзакционные свойства будущего PostgreSQL. Один хост/одна БД не дают высокой доступности.

## Запуск

Команды установки и запуска API/worker — в [README](../README.md). Frontend запускается отдельно по [frontend/README.md](../frontend/README.md). Для подготовки зависимостей без API-ключей достаточно Python 3.11+ и Node версии, указанной в frontend.

API: `python -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --no-proxy-headers` из backend.

Worker: `python -m app.worker` из backend. Для одного прохода: `python -m app.worker --once`.

Без worker сообщения остаются queued. `/health/live` проверяет процесс, `/health/ready` — доступность хранилища; readiness не подтверждает работу отдельного worker или стороннего провайдера. Runtime-файлы лежат в backend/var и исключены из Git.

Конфигурация читается из корневого `.env` и окружения процесса. В `.env.example` три секретных значения остаются пустыми. По умолчанию Origin разрешён для localhost/127.0.0.1 на 3000 и 8000. Адреса задаются JSON-массивом ALLOWED_ORIGINS. Cookie Secure=false допустима только в локальном HTTP-режиме.

## Сценарий чата

1. `POST /api/v1/session` с Origin выдаёт HttpOnly cookie и `data.csrf_token`. Повтор действующей сессии возвращает её CSRF. TTL по умолчанию 24 часа от создания.
2. `POST /api/v1/conversations` с cookie, Origin и X-CSRF-Token создаёт диалог.
3. `POST /api/v1/conversations/{id}/turns` принимает закрытый JSON и Idempotency-Key длиной 8–128 символов (буквы/цифры/дефис/подчёркивание).

```json
{
  "text": "Есть ли этот товар?",
  "asset_ids": [],
  "language": "ru",
  "page_product_id": null
}
```

4. Ответ 202 содержит `data.turn.id`, status=queued и created=true. Повтор тела/ключа возвращает тот же turn с created=false. Изменённое тело даёт 409/idempotency_conflict. Второй запрос в занятый диалог — 409/conversation_busy.
5. `GET /api/v1/turns/{id}` опрашивается примерно раз в 1–2 секунды до completed/failed/cancelled. `POST /api/v1/turns/{id}/cancel` отменяет текущую обработку. Уже завершённый turn остаётся завершённым.
6. `GET /api/v1/conversations/{id}/messages` возвращает последние 50 сообщений в прямом порядке. Это ограниченное окно, не полный экспорт.

Успех: `{data, meta: {request_id, warnings}}`. Ошибка: `{error: {code, message, retryable}, meta}`. Содержимое исключений, исходный некорректный ввод, секреты и prompts не возвращаются клиенту.

Полное тело, включая порядок asset_ids, язык, page_product_id и conversation_id, участвует в hash идемпотентности. Для retry нужно повторять тот же ключ и payload. Уже сохранённый turn возвращается без повторного обращения к сервису документов.

## Подтверждение корзины

`POST /api/v1/cart/proposals` принимает только items с product_id и quantity. Цены и имя покупателя из тела не принимаются. Ответ ProposalView содержит серверные строки с именами/артикулами, unit price, line total, общую сумму, валюту, mode, version, cart_version и expires_at.

`POST /api/v1/proposals/{id}/confirm` принимает `{version}` и Idempotency-Key; сессия определяется сервером. Подтверждающий UI показывает именно этот snapshot. При истечении TTL или изменении значимых данных требуется новое предложение.

Доменная логика использует payload_hash. HTTP-фасад должен хранить привязку своей версии предложения к этому hash; нельзя подставлять произвольный клиентский hash или автоматически подтверждать изменённое предложение.

Чтение: `GET /api/v1/cart`, `GET /api/v1/proposals/{id}`. Отказ: `POST /api/v1/proposals/{id}/reject`. Неизвестный исход действия — action_outcome_unknown с retryable=false: нужно проверить состояние, а не автоматически создавать новую команду. Cart URL ограничен проверенным same-origin путём.

## Next.js и общая схема

Frontend использует Next.js App Router и TypeScript. Предпочтительно: браузер → `/api/v1/*` на origin frontend → rewrite/proxy → FastAPI. Бизнес-логику Python не дублируем в Next route handlers. Прокси сохраняет Cookie, Set-Cookie, Origin, X-CSRF-Token, Idempotency-Key.

BACKEND_INTERNAL_URL — серверная настройка frontend, не публичный ключ. EKT_API_USER, EKT_API_PASSWORD и LLM_API_KEY не передаются в Next и никогда не получают префикс NEXT_PUBLIC_. Cookie HttpOnly; CSRF хранится в памяти клиента. После reload можно снова запросить `/session`.

Типы берутся из OpenAPI. После изменения HTTP DTO выполнить из backend:

```powershell
.venv/Scripts/python.exe scripts/export_openapi.py
```

Сгенерированную схему коммитить вместе с изменением API и согласовать изменения API-клиента frontend. Внутренний [contracts.md](contracts.md) содержит другой уровень абстракции и не заменяет OpenAPI.

## Подключение данных и БД

Точка сборки — `build_container` в bootstrap.py. И HTTP, и worker вызывают одну фабрику. Регистрации только в HTTP entrypoint недостаточно: отдельный worker также должен получить адаптеры.

**CatalogPort:** search(query, limit), get_product(id), alternatives(id). Возвращает HTTP Product/SearchResult через mapper. Проверяет происхождение и конфликтность данных. Ошибка источника не должна превращаться в пустой каталог или нулевой остаток. Денежные значения — десятичные строки, неизвестные значения — null; coverage всегда явно указан.

**DocumentsPort:** resolve(session_id, asset_ids). Проверяет ownership, scan=clean и готовность всех объектов. Возвращает ParsedDocument 1:1 в исходном порядке, включая дубли. Чужой файл — отказ, а не пропуск. Worker повторяет проверку непосредственно перед обработкой. Upload ещё не включён.

**ActionsPort:** facade над доменными actions. Проверяет владельца каждой операции, TTL, версии, цену/остаток/количество и идемпотентность. propose не меняет корзину. Подтверждение и эффект защищаются durable хранением; локальная транзакция не откатывает HTTP-запись во внешней системе.

**StateStore:** синхронный интерфейс; HTTP/worker вызывают блокирующие методы в threadpool. Для PostgreSQL используется sync pool либо согласованный перевод всего порта в async. Обязательные гарантии:

- submit атомарно записывает сообщение, turn и job; unique(session_id, key) и сравнение canonical payload действуют при конкурентных запросах;
- replay проверяет ownership и hash; submit проверяет их повторно в своей транзакции;
- только один активный turn на conversation; нужна блокировка строки/constraint, а не незащищённый SELECT;
- claim атомарно записывает lease token; в PostgreSQL можно использовать FOR UPDATE SKIP LOCKED;
- finish проверяет активную сессию, статус, token и срок аренды и атомарно записывает ответ, сообщение и завершение job;
- истёкший running job не повторяется вслепую: состояние внешнего вызова может быть неизвестно;
- глобальный предел очереди и rate quotas атомарны между процессами;
- cleanup удаляет истёкшие сессии и связанные приватные данные; при остановленном worker cleanup не выполняется.

Для PostgreSQL адаптера повторить тесты конкурентности на реальной PostgreSQL. Текущие test_store_worker.py и тесты HTTP isolation — обязательный минимум, не замена отдельной проверки интеграции.

## Готовность к следующему этапу

Объединение включает обе реализации без force-push в main. До включения настоящей LLM и реальной корзины: согласовать источник данных, выполнить контрактные тесты, реализовать budgets и контроль неизвестных исходов, проверить файлы и политику данных. Синтетическое демо должно оставаться явно обозначенным на каждом экране с ценами/остатками.
