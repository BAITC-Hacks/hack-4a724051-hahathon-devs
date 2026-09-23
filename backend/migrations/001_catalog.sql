-- Каталог товаров и журнал синхронизаций.
-- raw хранит карточку источника как есть, Product строится из неё через product_from_source(),
-- чтобы правила качества данных жили в одном месте. Остальные колонки нужны для поиска и фильтров.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE products (
    id                bigint PRIMARY KEY,
    source            text        NOT NULL CHECK (source IN ('synthetic', 'ekt')),
    article           text        NOT NULL,
    supplier_article  text,
    barcode           text,
    name              text        NOT NULL,
    brand             text,
    category_path     text[]      NOT NULL DEFAULT '{}',
    price             numeric(14, 2),
    unit              text        NOT NULL,
    stock_status      text        NOT NULL,
    sellable_quantity integer,
    warnings          text[]      NOT NULL DEFAULT '{}',
    raw               jsonb       NOT NULL,
    fetched_at        timestamptz NOT NULL,
    updated_at        timestamptz NOT NULL DEFAULT now(),
    search_vector     tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('russian', coalesce(name, '')), 'A') ||
        setweight(to_tsvector('russian', coalesce(brand, '')), 'B') ||
        setweight(to_tsvector('russian', coalesce(raw ->> 'description', '')), 'C')
    ) STORED
);

CREATE INDEX products_article_idx          ON products (lower(article));
CREATE INDEX products_supplier_article_idx ON products (lower(supplier_article));
CREATE INDEX products_barcode_idx          ON products (barcode);
CREATE INDEX products_category_idx         ON products USING gin (category_path);
CREATE INDEX products_search_idx           ON products USING gin (search_vector);
CREATE INDEX products_name_trgm_idx        ON products USING gin (lower(name) gin_trgm_ops);

CREATE TABLE catalog_sync_runs (
    id                bigserial PRIMARY KEY,
    source            text        NOT NULL,
    status            text        NOT NULL CHECK (status IN ('running', 'complete', 'partial', 'failed')),
    started_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz,
    pages_read        integer     NOT NULL DEFAULT 0,
    products_upserted integer     NOT NULL DEFAULT 0,
    products_failed   integer     NOT NULL DEFAULT 0,
    last_page         integer,
    error             text
);
