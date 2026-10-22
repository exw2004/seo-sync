"""Database writes.

Every upsert here is idempotent: re-running a sync for a date range that was
already pulled updates the existing rows rather than duplicating them. That is
what makes the trailing-window re-pull safe, and what lets a failed cron run
self-heal on the next night without anyone intervening.

Identity is a SHA-256 of the dimension values rather than a composite unique key
on the columns themselves. A natural key over property+date+query+page+country+
device+search_type exceeds InnoDB's 3072-byte index limit under utf8mb4 once the
query and page columns are wide enough to be useful, and truncating them with
prefix lengths would silently collapse distinct long URLs into one row.
"""
from __future__ import annotations

import hashlib
from typing import Iterable, Iterator, Sequence

# Tuned for MySQL's max_allowed_packet under a default shared-hosting config.
# Larger batches stop helping well before they start failing.
BATCH_SIZE = 500

GSC_COLUMNS = (
    "row_key",
    "property",
    "date",
    "query",
    "page",
    "country",
    "device",
    "search_type",
    "dim_set",
    "clicks",
    "impressions",
    "ctr",
    "position",
)

_GSC_KEY_FIELDS = (
    "property",
    "date",
    "query",
    "page",
    "country",
    "device",
    "search_type",
    # Without this, the "date" grain's row for a day and the "date+page" grain's
    # row for that day with page="" would hash identically and overwrite each
    # other, silently corrupting both.
    "dim_set",
)


def gsc_row_key(row: dict) -> str:
    """Stable identity hash for one Search Console row.

    Each field is length-prefixed rather than joined with a separator. A plain
    separator is ambiguous -- ("a<sep>b", "c") and ("a", "b<sep>c") serialize
    identically -- which would silently merge two distinct rows into one.
    Length prefixes make the encoding injective for any field contents.
    """
    payload = "".join(
        f"{len(v)}:{v}" for v in (str(row.get(f, "")) for f in _GSC_KEY_FIELDS)
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _batched(rows: Iterable[dict], size: int) -> Iterator[list[dict]]:
    batch: list[dict] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def upsert_gsc_rows(
    conn,
    rows: Iterable[dict],
    table: str = "gsc_daily",
    batch_size: int = BATCH_SIZE,
) -> int:
    """Insert or update Search Console rows. Returns the number of rows sent.

    The caller owns the connection and the transaction boundary; this commits
    once per batch so a long backfill does not hold one enormous transaction.

    `table` is interpolated into the statement, so it must come from code and
    never from user input. Callers pass a literal.
    """
    placeholders = ", ".join(["%s"] * len(GSC_COLUMNS))
    collist = ", ".join(f"`{c}`" for c in GSC_COLUMNS)
    sql = (
        f"INSERT INTO `{table}` ({collist}) VALUES ({placeholders}) "
        "ON DUPLICATE KEY UPDATE "
        "clicks = VALUES(clicks), "
        "impressions = VALUES(impressions), "
        "ctr = VALUES(ctr), "
        "`position` = VALUES(`position`), "
        "fetched_at = CURRENT_TIMESTAMP"
    )

    sent = 0
    for batch in _batched(rows, batch_size):
        params = []
        for row in batch:
            row = dict(row)
            row["row_key"] = gsc_row_key(row)
            params.append(tuple(row.get(c) for c in GSC_COLUMNS))
        with conn.cursor() as cur:
            cur.executemany(sql, params)
        conn.commit()
        sent += len(batch)
    return sent


def date_bounds(conn, table: str = "gsc_daily", property_: str | None = None):
    """(min_date, max_date) already stored, or (None, None) if the table is empty.

    Used to report what a backfill actually has to do before it does it.
    """
    sql = f"SELECT MIN(`date`) AS lo, MAX(`date`) AS hi FROM `{table}`"
    params: Sequence = ()
    if property_:
        sql += " WHERE `property` = %s"
        params = (property_,)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    if not row:
        return None, None
    if isinstance(row, dict):
        return row.get("lo"), row.get("hi")
    return row[0], row[1]
