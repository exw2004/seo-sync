"""Tests for the archive health checks. No database, no network, no credentials.

Run with:  python tests/test_health.py
"""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from seo_sync.health import Finding, evaluate  # noqa: E402

TODAY = dt.date(2026, 9, 22)


def days(start: dt.date, n: int, impressions: int):
    """n consecutive days ending the day before `start + n`."""
    return [(start + dt.timedelta(days=i), impressions, 1) for i in range(n)]


def codes(findings):
    return [f.code for f in findings]


# --- empty ------------------------------------------------------------------
assert codes(evaluate([], TODAY)) == ["empty"]
print("empty archive reported")

# --- healthy ----------------------------------------------------------------
# 60 steady days ending 2 days ago, which is normal Search Console lag.
healthy = days(TODAY - dt.timedelta(days=61), 60, 100)
assert evaluate(healthy, TODAY) == [], evaluate(healthy, TODAY)
print("healthy archive is silent")

# Lag of exactly the threshold is still healthy -- the boundary must not alert,
# or normal GSC processing delay produces weekly false alarms.
lagged = days(TODAY - dt.timedelta(days=64), 60, 100)
newest = max(r[0] for r in lagged)
assert (TODAY - newest).days == 5
assert codes(evaluate(lagged, TODAY)) == [], "boundary should not alert"
print("stale boundary (exactly 5 days) stays silent")

# One day past it does alert.
stale = days(TODAY - dt.timedelta(days=65), 60, 100)
assert (TODAY - max(r[0] for r in stale)).days == 6
f = evaluate(stale, TODAY)
assert codes(f) == ["stale"], codes(f)
assert "6 days" in f[0].summary
print("stale at 6 days alerts:", f[0].summary)

# --- a long silent stop ----------------------------------------------------
# Collected steadily, then stopped dead months ago.
stopped = days(dt.date(2025, 11, 1), 200, 90)  # ends 2026-05-19
f = evaluate(stopped, TODAY)
assert codes(f) == ["stale"], codes(f)
assert "2026-05-19" in f[0].detail
# A stale archive must be reported ALONE: with no recent data the collapse
# comparison is arithmetic on nothing and would add a misleading second alarm.
assert len(f) == 1
print("long silent stop detected:", f[0].summary)

# --- collapse ---------------------------------------------------------------
# 28 days at 100 impressions, then 7 days at 5.
base = days(TODAY - dt.timedelta(days=34), 28, 100)
recent = days(TODAY - dt.timedelta(days=6), 7, 5)
f = evaluate(base + recent, TODAY)
assert codes(f) == ["collapse"], codes(f)
assert "95%" in f[0].summary, f[0].summary
print("collapse detected:", f[0].summary)

# A mild dip is not a collapse.
mild = days(TODAY - dt.timedelta(days=6), 7, 60)
assert evaluate(base + mild, TODAY) == []
print("mild dip stays silent")

# --- the small-numbers guard ------------------------------------------------
# A small site averaging ~1.4 impressions/day. A week at zero against that
# baseline is a 100% "drop" that means nothing. The check must decline.
tiny_base = days(TODAY - dt.timedelta(days=34), 28, 1)
tiny_recent = days(TODAY - dt.timedelta(days=6), 7, 0)
assert evaluate(tiny_base + tiny_recent, TODAY) == [], "fired on noise"
print("low-traffic site does not produce collapse noise")

# Same shape with real volume does fire, proving the guard is about magnitude
# and not about the zero.
big_base = days(TODAY - dt.timedelta(days=34), 28, 20)
big_recent = days(TODAY - dt.timedelta(days=6), 7, 0)
assert codes(evaluate(big_base + big_recent, TODAY)) == ["collapse"]
print("same shape at real volume does fire")

# --- median, not mean -------------------------------------------------------
# One viral day must not raise the bar enough to make a normal week look like a
# collapse. Mean of 27x10 + 1x2000 is ~84/day; median is 10.
spiky = days(TODAY - dt.timedelta(days=34), 27, 10) + [
    (TODAY - dt.timedelta(days=8), 2000, 50)
]
normal = days(TODAY - dt.timedelta(days=6), 7, 10)
assert evaluate(spiky + normal, TODAY) == [], "a spike set the baseline"
print("one viral day does not set the baseline")

# --- shape ------------------------------------------------------------------
for f in evaluate(stale, TODAY) + evaluate(base + recent, TODAY):
    assert isinstance(f, Finding) and f.summary and f.detail
    assert "\n" not in f.summary, "summary goes in a subject line"
print("findings are well-formed")

print("\nALL HEALTH CHECKS PASS")
