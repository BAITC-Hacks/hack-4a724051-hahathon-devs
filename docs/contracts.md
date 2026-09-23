# Интерфейсы между частями

Договорённость между участником 1 (ассистент, поиск, аналоги, корзина: логика) и участником 2 (HTTP API, сессии, БД, импорт каталога, разбор файлов, фронтенд). Логика написана через порты, поэтому её можно запускать и тестировать с реализациями в памяти (`backend/app/adapters/memory.py`), пока нет БД.

## Что реализует участник 2

| Порт | Файл | Суть |
| --- | --- | --- |
| `CatalogReader` | `app/modules/catalog/ports.py` | `get_product(id, fresh)`, `find_by_identifier(value)`, `search_text(query, limit)`, `list_category(path, limit)` |
| `ProposalStore` | `app/modules/actions/ports.py` | `save`, `get(session_id, id)`, `open_for_session`, `compare_and_set_status` |
| `CartStore` | `app/modules/actions/ports.py` | `get_cart(session_id)`, `apply_to_cart(session_id, additions, expected_version, operation_id)` |
| `ConversationStore` | `app/modules/chat/service.py` | `history(session_id, limit)`, `append(session_id, role, content)` |

Важные требования:

- Импорт каталога превращает сырую карточку в `Product` только через `product_from_source(raw)` из `app/modules/catalog/quality.py`. Там все правила: продающие склады, характеристики, противоречия, пустая цена.
- `get_product(id, fresh=True)` перечитывает цену и остаток из EKT API. Если API не ответил, вернуть товар со `stock.status = UNKNOWN`, а не нулевой остаток.
- `find_by_identifier` ищет точное совпадение по `article`, `supplier_article` и штрихкоду без учёта регистра.
- Все методы хранилищ ограничены `session_id`. Чужой proposal или корзина должны не находиться.
- `compare_and_set_status` атомарный: `UPDATE proposals SET status=? WHERE id=? AND status=?` и проверка числа изменённых строк.
- `apply_to_cart` одной транзакцией проверяет версию корзины, прибавляет количества, повышает версию и записывает `operation_id` с уникальным индексом. Повтор с тем же `operation_id` возвращает уже применённую корзину. Если версия не совпала, бросить `CartVersionConflict`.
- Файлы клиента после проверки передаются в чат как `Attachment` (`app/modules/chat/service.py`): `kind="text"` для извлечённого текста xlsx/docx/pdf, `kind="image"` или `kind="pdf"` с base64, если модель должна прочитать исходник сама. Лимиты и статусы разбора пишутся в `note`.

## Что вызывает HTTP-слой

```python
from app.modules.chat.factory import build_chat_service

chat = build_chat_service(catalog, proposals, carts, conversations, cart_url="/cart")

chat.handle_message(session_id, text, attachments=[...], page_product_id=None)  # POST сообщения
chat.confirm(session_id, proposal_id, payload_hash)  # кнопка «Добавить в корзину»
chat.reject(session_id, proposal_id)                  # кнопка «Не надо»
```

Все три метода возвращают `ChatReply`, в JSON через `reply.to_dict()`. `session_id` берётся только из серверной сессии, не из тела запроса.

Кнопка подтверждения отправляет `proposal.id` и `proposal.payload_hash` из последнего ответа. Если клиент пишет «да, добавь» текстом, `handle_message` сам находит текущее предложение и подтверждает его без вызова модели.

## Пример ответа чата

```json
{
  "message": "Подготовил, подтвердите.",
  "products": [
    {
      "id": 900001,
      "name": "778636 АВ ВА47-29 1P 10А C 4,5кА IEK",
      "article": "990100001_",
      "brand": "IEK",
      "price": "1350",
      "currency": "KZT",
      "unit": "шт",
      "stock_status": "in_stock",
      "sellable_quantity": 76,
      "stores": [{"name": "Алматы", "quantity": 40}, {"name": "Нур-Султан", "quantity": 36}],
      "url": "/catalog/nizkovoltnaya_apparatura/modulnye_avtomaticheskie_vyklyuchateli/778636_av_va47_29_1p_10a_c_4_5ka_iek/",
      "image": null,
      "certificates": [],
      "attributes": [{"label": "Номинальный ток", "value": "10А", "conflict": false}],
      "warnings": []
    }
  ],
  "proposal": {
    "id": "fb16f9fa157d497b8561cdd36cdcefd6",
    "status": "proposed",
    "items": [{"product_id": 900001, "name": "778636 АВ ВА47-29 1P 10А C 4,5кА IEK", "article": "990100001_",
               "quantity": 2, "unit": "шт", "unit_price": "1350", "line_total": "2700"}],
    "total": "2700",
    "currency": "KZT",
    "expires_at": "2026-09-23T09:41:45.626258+00:00",
    "payload_hash": "dbb860...",
    "cart_url": null
  },
  "sources": [],
  "mode": "llm",
  "warnings": []
}
```

- `stock_status`: `in_stock`, `out_of_stock`, `not_sellable` (остаток только на складах брака/образцов), `unknown` (не удалось проверить, это не ноль).
- `attributes[].conflict = true` значит, что источники расходятся. Значение `null`, показывать как «уточняется».
- `proposal.status`: `proposed`, `executing`, `applied`, `rejected`, `expired`, `stale`, `failed`. Кнопку подтверждения показывать только для `proposed`. После `applied` в `cart_url` ссылка на корзину.
- `mode`: `llm` ответ модели прошёл проверку; `verified_fallback` текст модели не прошёл проверку фактов и заменён текстом из данных; `fallback` модель выключена или недоступна; `action` ответ на подтверждение/отказ.
- Деньги приходят строками, чтобы не терять точность.

## Данные для демо

- Синтетический каталог: `data/synthetic/ekt_products.json`, тот же формат, что `/api/products/detail`, плюс поля `certificates` и `properties.EDINITSA_IZMERENIYA`. Пересоздать: `python data/synthetic/generate.py`.
- Заглушки сертификатов: `data/synthetic/certificates/SYN-<id>.pdf`, в карточках ссылка `/certificates/SYN-<id>.pdf`. Их нужно раздавать статикой по этому пути.
- Условия покупки: `data/curated/purchase_terms.json`, собрано с публичных страниц ekt.kz, у каждой темы есть источник.

## Переменные окружения ассистента

| Переменная | По умолчанию | Смысл |
| --- | --- | --- |
| `LLM_ENABLED` | `false` | Без `true` модель не вызывается вообще, чат отвечает поиском и шаблонами |
| `LLM_API_KEY` | пусто | Ключ Anthropic. Если пусто, SDK берёт `ANTHROPIC_API_KEY` |
| `LLM_MODEL` | `claude-opus-5` | Модель Claude |
| `LLM_EFFORT` | `low` | Глубина рассуждений. `low` ради ответа за несколько секунд |
| `LLM_MAX_STEPS` | `5` | Сколько раз за ход можно обратиться к модели |
| `LLM_MAX_TOOL_CALLS` | `12` | Лимит вызовов инструментов за ход |
| `LLM_TIMEOUT_S` | `25` | Таймаут одного запроса к модели |
