-- Файлы клиентов. Исходник лежит в приватном хранилище по storage_key,
-- здесь владелец, результат проверки и извлечённый текст. Всё удаляется по expires_at.

CREATE TABLE assets (
    id           uuid        PRIMARY KEY,
    session_id   text        NOT NULL,
    filename     text        NOT NULL,
    kind         text        NOT NULL CHECK (kind IN ('xlsx', 'docx', 'pdf', 'jpeg', 'png')),
    size_bytes   integer     NOT NULL,
    sha256       text        NOT NULL,
    storage_key  text        NOT NULL,
    scan_status  text        NOT NULL CHECK (scan_status IN ('clean', 'skipped_demo')),
    status       text        NOT NULL CHECK (status IN ('ready', 'partial', 'failed')),
    error        text,
    text         text        NOT NULL DEFAULT '',
    warnings     text[]      NOT NULL DEFAULT '{}',
    created_at   timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL
);

CREATE INDEX assets_session_idx ON assets (session_id, created_at);
CREATE INDEX assets_expires_idx ON assets (expires_at);
