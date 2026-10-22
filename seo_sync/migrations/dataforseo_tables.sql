-- DataForSEO results, stored locally.
--
-- Template. Each project copies this into its own migrations directory and
-- applies it to its own database. Projects may share one DataForSEO account
-- and balance -- that is fine, it is outbound purchased data with no shared
-- state -- but never share rows.
--
-- Unlike gsc_daily this is PURCHASED data, not our own. Every row cost money,
-- so nothing here is ever re-fetched casually: query the table, not the API.

-- Where we rank, over time. One row per keyword/location/device/date/surface.
-- `surface` is 'organic' or 'local_pack' -- the local pack is the entire
-- reason to pay for this, since Search Console cannot see it at all.
--
-- position NULL means genuinely not present, which is different information
-- from ranking badly. Do not store 0 or 100 for it.
CREATE TABLE IF NOT EXISTS dfs_serp_rankings (
    id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    checked_on   DATE            NOT NULL,
    keyword      VARCHAR(512)    NOT NULL,
    location     VARCHAR(128)    NOT NULL COMMENT 'location_code or lat,lng,zoom as sent',
    device       VARCHAR(16)     NOT NULL DEFAULT 'desktop',
    surface      VARCHAR(16)     NOT NULL COMMENT 'organic | local_pack',
    position     INT UNSIGNED    NULL COMMENT 'NULL = not present at all',
    url          VARCHAR(1024)   NOT NULL DEFAULT '',
    top_results  JSON            NULL COMMENT 'who outranked us, for competitor work',
    cost_usd     DECIMAL(10,6)   NOT NULL DEFAULT 0,
    PRIMARY KEY (id),
    UNIQUE KEY uq_dfs_rank (checked_on, keyword(190), location, device, surface),
    KEY idx_dfs_keyword (keyword(64), checked_on)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Search volume and competition per keyword. Refreshed rarely -- these move
-- slowly and every refresh is billable.
CREATE TABLE IF NOT EXISTS dfs_keyword_metrics (
    id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    fetched_on     DATE            NOT NULL,
    keyword        VARCHAR(512)    NOT NULL,
    location_code  INT             NOT NULL,
    search_volume  INT             NULL,
    cpc            DECIMAL(10,4)   NULL,
    competition    DECIMAL(6,4)    NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_dfs_kw (fetched_on, keyword(190), location_code),
    KEY idx_dfs_kw_vol (search_volume)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Every billable call, so local records reconcile against the dashboard and
-- an unexpected bill can be traced to the run that caused it.
CREATE TABLE IF NOT EXISTS dfs_api_spend (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    called_at   TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    run_label   VARCHAR(64)     NOT NULL DEFAULT '',
    label       VARCHAR(255)    NOT NULL,
    cost_usd    DECIMAL(10,6)   NOT NULL,
    PRIMARY KEY (id),
    KEY idx_dfs_spend_at (called_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
