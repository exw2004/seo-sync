-- Search Console Search Analytics archive.
--
-- Template. Each project copies this into its own migrations directory and
-- applies it to its own database. The table is never shared between projects.
--
-- One row per (property, date, query, page, country, device, search_type, dim_set).
--
-- `dim_set` names the grain the row came from -- "date", "date+page",
-- "date+query" and so on. Google withholds rows that too few users generated,
-- and the finer the slice the more it withholds, so each grain is pulled and
-- stored separately. Coarse grains carry totals you can trust; fine grains
-- carry whatever detail survives. NEVER SUM ACROSS GRAINS: each one is a
-- complete view of the same traffic at a different resolution, so mixing them
-- multiply-counts. Always filter on dim_set.
-- `row_key` is a SHA-256 of exactly those fields -- see seo_sync.store.gsc_row_key.
-- A natural composite key over the same columns would exceed InnoDB's 3072-byte
-- index limit under utf8mb4.

CREATE TABLE IF NOT EXISTS gsc_daily (
    id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    row_key      CHAR(64)        NOT NULL COLLATE ascii_bin,
    property     VARCHAR(255)    NOT NULL,
    `date`       DATE            NOT NULL,
    `query`      VARCHAR(512)    NOT NULL DEFAULT '',
    page         VARCHAR(1024)   NOT NULL DEFAULT '',
    country      VARCHAR(8)      NOT NULL DEFAULT '',
    device       VARCHAR(16)     NOT NULL DEFAULT '',
    search_type  VARCHAR(16)     NOT NULL DEFAULT 'web',
    dim_set      VARCHAR(32)     NOT NULL DEFAULT 'date',
    clicks       INT UNSIGNED    NOT NULL DEFAULT 0,
    impressions  INT UNSIGNED    NOT NULL DEFAULT 0,
    ctr          DECIMAL(9,8)    NOT NULL DEFAULT 0,
    `position`   DECIMAL(8,4)    NOT NULL DEFAULT 0,
    fetched_at   TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_gsc_row (row_key),
    KEY idx_gsc_date (`date`),
    KEY idx_gsc_dimset (dim_set, `date`),
    KEY idx_gsc_prop_date (property, `date`),
    KEY idx_gsc_query (`query`(64)),
    KEY idx_gsc_page (page(255))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
