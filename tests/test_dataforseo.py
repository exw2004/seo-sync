"""Tests for the DataForSEO client. No network, no account, no balance.

Run with:  python tests/test_dataforseo.py

Everything that spends money is behind CostGuard, so the guard is what gets
tested hardest. A bug here costs real cash, and an agent can drive this module
with no user watching.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from seo_sync.dataforseo import (  # noqa: E402
    BudgetExceeded,
    CostGuard,
    estimate,
    find_domain,
    parse_serp,
)

# --- estimates, with no network --------------------------------------------
# A local-pack plan: 6 local queries x 5 locations, weekly.
plan = estimate(serp_keywords=6 * 5 * 4, serp_live=True)
assert abs(plan["total"] - 0.24) < 0.001, plan
print(f"local-pack plan, one month:    ${plan['total']:.4f}")

# A volume check: one search-volume task, 1000 keywords.
plan = estimate(search_volume_tasks=1)
assert abs(plan["total"] - 0.06) < 1e-9, plan
print(f"1,000-keyword volume check:     ${plan['total']:.4f}")

# Competitor sweep: 10 Labs requests at 1000 rows each.
plan = estimate(labs_requests=10, labs_rows_each=1000)
assert abs(plan["total"] - 1.32) < 1e-9, plan
print(f"10 Labs requests x 1000 rows:   ${plan['total']:.4f}")

# The whole first-year plan together.
plan = estimate(serp_keywords=6 * 5 * 52, labs_requests=10, search_volume_tasks=2)
print(f"Full first-year plan:           ${plan['total']:.2f}")
assert plan["total"] < 10, "first-year plan should be well under the $50 minimum"

# --- the guard --------------------------------------------------------------
g = CostGuard(budget_usd=0.01)
g.reserve(0.002, "serp:a")
g.record(0.002, "serp:a")
assert abs(g.spent_usd - 0.002) < 1e-9
assert abs(g.remaining - 0.008) < 1e-9
print(f"guard tracks spend: ${g.spent_usd:.4f} spent, ${g.remaining:.4f} left")

# Crossing the budget must raise BEFORE anything is sent, and must not move
# the counter -- a refused call spends nothing.
try:
    g.reserve(0.05, "serp:huge")
    raise SystemExit("guard did not stop an over-budget call")
except BudgetExceeded as e:
    assert "Nothing was sent" in str(e)
    assert abs(g.spent_usd - 0.002) < 1e-9, "a refused call moved the counter"
print("over-budget call refused before sending, counter unchanged")

# The runaway case this exists for: a loop over a keyword list that turned out
# to be far longer than expected. It must stop, not drain the balance.
g2 = CostGuard(budget_usd=0.10)
sent = 0
try:
    for _ in range(10_000):
        g2.reserve(0.002, "serp:loop")
        g2.record(0.002, "serp:loop")
        sent += 1
except BudgetExceeded:
    pass
assert sent == 50, sent
assert g2.spent_usd <= 0.10 + 1e-9
print(f"runaway loop stopped after {sent} calls at ${g2.spent_usd:.2f}, cap held")

# --- parsing a SERP ---------------------------------------------------------
# Shaped like a real local-intent response: a map pack above organic results.
fixture = {
    "cost": 0.002,
    "tasks": [{
        "result": [{
            "items": [
                {"type": "local_pack", "rank_absolute": 1, "rank_group": 1,
                 "title": "The Book Nook", "domain": "booknook.example",
                 "url": "https://booknook.example/", "rating": {"value": 4.8}},
                {"type": "local_pack", "rank_absolute": 2, "rank_group": 2,
                 "title": "All My Friends Books",
                 "domain": "example.com",
                 "url": "https://example.com/", "rating": {"value": 5.0}},
                {"type": "paid", "rank_absolute": 3, "domain": "amazon.com"},
                {"type": "organic", "rank_absolute": 4, "rank_group": 1,
                 "domain": "yelp.com", "url": "https://yelp.com/x", "title": "Yelp"},
                {"type": "organic", "rank_absolute": 5, "rank_group": 2,
                 "domain": "www.example.com",
                 "url": "https://example.com/", "title": "Example"},
            ]
        }]
    }],
}
p = parse_serp(fixture, "example query")
assert len(p["local_pack"]) == 2, p["local_pack"]
assert len(p["organic"]) == 2, p["organic"]
# A paid result is neither, and must not be counted as an organic position.
assert all(o["domain"] != "amazon.com" for o in p["organic"])
assert p["cost"] == 0.002
print(f"parsed: {len(p['local_pack'])} local-pack, {len(p['organic'])} organic, "
      f"ads excluded")

# --- finding ourselves ------------------------------------------------------
lp = find_domain(p["local_pack"], "example.com")
assert lp and lp["rank_group"] == 2, lp
# The organic entry carries www.; a www/non-www mismatch must not read as
# "not ranking", which is the exact error that makes a tracker useless.
org = find_domain(p["organic"], "example.com")
assert org and org["rank_group"] == 2, org
print("found in both, www/non-www normalized")

# Absent must be None, not a sentinel position -- not ranking and ranking
# badly are different facts and must be stored differently.
assert find_domain(p["organic"], "nowhere.example") is None
print("absent domain returns None, not a fake position")

# --- a failed task must not read as "not ranking" ---------------------------
# The real case, hit 2026-09-22: an unverified account returns HTTP 200 with a
# task-level 40104 and result: null. That flattens to zero items, which is
# indistinguishable from a SERP we genuinely do not appear in. Storing it would
# write a measurement that never happened, and it would look like a real
# absence forever after.
for status, msg in ((40104, "Please verify your account before using the API."),
                    (40200, "Payment Required."),
                    (40501, "Invalid Field.")):
    bad = {"cost": 0, "tasks": [{"status_code": status, "status_message": msg,
                                 "result": None}]}
    try:
        parse_serp(bad, "example query")
        raise SystemExit(f"task status {status} was silently treated as empty")
    except RuntimeError as e:
        assert str(status) in str(e) and "example query" in str(e), str(e)
        assert "must not be stored as an absent one" in str(e)
print("failed task raises instead of reporting a phantom absence")

# A successful task with genuinely zero items is still valid and must NOT raise
# -- that is a real "we do not appear here" and is worth recording.
ok_empty = {"cost": 0.002,
            "tasks": [{"status_code": 20000, "status_message": "Ok.",
                       "result": [{"items": []}]}]}
p2 = parse_serp(ok_empty, "nowhere query")
assert p2["organic"] == [] and p2["local_pack"] == []
print("genuine empty SERP still parses cleanly (a real absence is data)")

print("\nALL DATAFORSEO CHECKS PASS")
