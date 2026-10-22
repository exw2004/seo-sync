import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from seo_sync import store

class Cur:
    def __init__(s, log): s.log = log
    def __enter__(s): return s
    def __exit__(s, *a): return False
    def executemany(s, sql, params): s.log.append(("executemany", sql, params))
    def execute(s, sql, params=()): s.log.append(("execute", sql, params))
    def fetchone(s): return {"lo": None, "hi": None}

class Conn:
    def __init__(s): s.log = []; s.commits = 0
    def cursor(s): return Cur(s.log)
    def commit(s): s.commits += 1

base = dict(property="sc-domain:example.com", date="2026-09-01", query="q",
            page="https://example.com/", country="usa", device="DESKTOP",
            search_type="web", dim_set="date+query", clicks=1, impressions=2, ctr=0.5, position=3.0)

# batching: 1201 rows at size 500 -> 3 batches (500,500,201), 3 commits
rows = [dict(base, query=f"q{i}") for i in range(1201)]
c = Conn()
n = store.upsert_gsc_rows(c, rows, "gsc_daily")
sizes = [len(p) for k, _, p in c.log if k == "executemany"]
assert n == 1201 and sizes == [500, 500, 201], (n, sizes)
assert c.commits == 3, c.commits
print(f"batching OK: {n} rows -> batches {sizes}, {c.commits} commits")

sql = c.log[0][1]
assert sql.count("%s") == len(store.GSC_COLUMNS) == 13, sql
assert "ON DUPLICATE KEY UPDATE" in sql
# identity columns must never be in the UPDATE clause, or a revision would
# rewrite the row into a different identity than its own hash
upd = sql.split("ON DUPLICATE KEY UPDATE")[1]
for col in ("row_key", "property", "`date`", "`query`", "page", "country", "device", "search_type", "dim_set"):
    assert col not in upd, f"identity column {col} is in the UPDATE clause"
for col in ("clicks", "impressions", "ctr", "`position`", "fetched_at"):
    assert col in upd, f"metric {col} missing from UPDATE clause"
print("upsert SQL OK: 13 placeholders, metrics-only UPDATE clause")

# row_key is computed and placed in the right position
first = c.log[0][2][0]
assert first[0] == store.gsc_row_key(rows[0]) and len(first) == 13
assert first[1] == base["property"]
print("row_key positioned correctly in params")

# empty input writes nothing
c2 = Conn()
assert store.upsert_gsc_rows(c2, [], "gsc_daily") == 0 and c2.commits == 0
print("empty input OK: no statement, no commit")
