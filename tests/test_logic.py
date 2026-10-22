"""Pure-logic tests -- no network, no database, no credentials.

Run with:  python tests/test_logic.py  &&  python tests/test_upsert.py

The Google client libraries are stubbed on import: nothing exercised here may
depend on them, which keeps these runnable on a bare checkout.
"""
import sys, types, datetime as dt
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
# Stub the Google client libs: this test covers only the pure logic, which must
# not depend on them being installed.
for name in ("google", "google.oauth2", "google.oauth2.credentials",
             "googleapiclient", "googleapiclient.discovery", "googleapiclient.errors"):
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["google.oauth2"].service_account = types.SimpleNamespace(
    Credentials=types.SimpleNamespace(from_service_account_file=lambda p, scopes=None: ("sa", p)))
sys.modules["google.oauth2.credentials"].Credentials = types.SimpleNamespace(
    from_authorized_user_file=lambda p, scopes=None: ("user", p))
sys.modules["googleapiclient.discovery"].build = lambda *a, **k: None
class _HttpError(Exception): pass
sys.modules["googleapiclient.errors"].HttpError = _HttpError
from seo_sync import store
from seo_sync.gsc import month_chunks, retention_floor, _normalize, GRAINS, DEFAULT_GRAINS

today = dt.date(2026, 9, 22)

# --- month_chunks covers the range exactly, no gaps, no overlap ---
lo = retention_floor(today)
chunks = list(month_chunks(lo, today))
assert chunks[0][0] == lo, chunks[0]
assert chunks[-1][1] == today, chunks[-1]
for (a1, b1), (a2, _) in zip(chunks, chunks[1:], strict=False):
    assert b1 + dt.timedelta(days=1) == a2, (b1, a2)
    assert a1 <= b1
print(f"month_chunks: {len(chunks)} chunks, {lo} .. {today}, contiguous OK")

# year boundary
yb = list(month_chunks(dt.date(2025,11,15), dt.date(2026,2,3)))
print("year boundary:", yb)
assert yb == [
    (dt.date(2025,11,15), dt.date(2025,11,30)),
    (dt.date(2025,12,1),  dt.date(2025,12,31)),
    (dt.date(2026,1,1),   dt.date(2026,1,31)),
    (dt.date(2026,2,1),   dt.date(2026,2,3)),
]

# single day
assert list(month_chunks(dt.date(2026,5,4), dt.date(2026,5,4))) == [(dt.date(2026,5,4), dt.date(2026,5,4))]
print("single-day range OK")

# --- normalize ---
row = {"keys": ["2026-09-01", "example query", "https://example.com/services", "usa", "MOBILE"],
       "clicks": 3, "impressions": 42, "ctr": 0.0714285, "position": 8.3}
n = _normalize(row, list(("date","query","page","country","device")),
               "sc-domain:example.com", "web", "date+query+page")
assert n["query"] == "example query" and n["clicks"] == 3 and n["search_type"] == "web"
assert set(n) == set(store.GSC_COLUMNS) - {"row_key"}, set(store.GSC_COLUMNS) ^ set(n)
print("normalize OK, columns match schema:", sorted(n))

# --- row_key: stable, and sensitive to each dimension ---
k = store.gsc_row_key(n)
assert k == store.gsc_row_key(dict(n)), "not stable"
assert len(k) == 64
seen = {k}
for f in ("property","date","query","page","country","device","search_type","dim_set"):
    v = dict(n); v[f] = "CHANGED"
    assert store.gsc_row_key(v) not in seen, f"collision when {f} changed"
    seen.add(store.gsc_row_key(v))
# metrics must NOT affect identity (that is what lets revisions upsert)
v = dict(n); v["clicks"] = 999; v["position"] = 1.0
assert store.gsc_row_key(v) == k, "metrics leaked into identity"
print("row_key: stable, dimension-sensitive, metric-independent OK")

# separator-injection: "a|b" + "c" must not collide with "a" + "b|c"
r1 = dict(n); r1["query"]="a\x00b"; r1["page"]="c"
r2 = dict(n); r2["query"]="a"; r2["page"]="b\x00c"
assert store.gsc_row_key(r1) != store.gsc_row_key(r2)
print("separator collision resistance OK")

# --- grains -----------------------------------------------------------------
# Coarse grains must come first: they are the most complete, and a reader
# scanning the table should meet the trustworthy totals before the sparse detail.
assert DEFAULT_GRAINS[0] == "date", DEFAULT_GRAINS
assert set(DEFAULT_GRAINS) == set(GRAINS)
for name, dims in GRAINS.items():
    assert dims[0] == "date", f"{name} must start with date"
    assert name == "+".join(dims), f"grain name {name!r} does not match dims {dims}"
print("grains OK:", list(DEFAULT_GRAINS))

# The bug this guards: without dim_set in the hash, the "date" grain row for a
# day and the "date+page" grain row for that day with page="" collide and
# silently overwrite each other.
a = {"property":"p","date":"2026-09-01","query":"","page":"","country":"","device":"",
     "search_type":"web","dim_set":"date"}
b = dict(a, dim_set="date+page")
assert store.gsc_row_key(a) != store.gsc_row_key(b), "grain collision"
print("grain collision guard OK")

# --- credential detection --------------------------------------------------
# Detected by file CONTENT, not filename or a flag, so a project switches
# between a service account key and a user OAuth token by swapping the file.
import json as _json, tempfile as _tf, os as _os
from seo_sync.gsc import load_credentials

def _tmp(obj):
    f = _tf.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    _json.dump(obj, f); f.close(); return f.name

sa = _tmp({"type": "service_account", "client_email": "x@y.iam.gserviceaccount.com",
           "private_key": "-----BEGIN PRIVATE KEY-----"})
assert load_credentials(sa)[0] == "sa"

# The real shape InstalledAppFlow writes for a user token.
usr = _tmp({"token": "ya29...", "refresh_token": "1//0g...", "client_id": "c.apps.googleusercontent.com",
            "client_secret": "GOCSPX-...", "token_uri": "https://oauth2.googleapis.com/token",
            "scopes": ["https://www.googleapis.com/auth/drive.readonly"]})
assert load_credentials(usr)[0] == "user"
print("credential detection OK: service_account and user OAuth token")

# A file that is neither must fail loudly at load, not at the first API call --
# a cron that dies on an unreadable credential should say so in its own log.
bad = _tmp({"client_id": "present", "note": "no refresh_token, no type"})
try:
    load_credentials(bad); raise SystemExit("should have raised")
except ValueError as e:
    assert "refresh_token" in str(e) and "service_account" in str(e), str(e)
print("malformed credential rejected with a useful message")
for f in (sa, usr, bad): _os.unlink(f)
