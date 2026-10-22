"""Detect that a Search Console archive has stopped being useful.

A property can silently stop collecting for months before anyone notices, and
that data is gone permanently -- Search Console never backfills. The console
itself shows only a chart trailing to zero, which you have to be looking at to see.

Two checks, catching different failures:

STALE -- the newest day in the archive is too far behind today.
    Catches that failure and everything shaped like it: a lost DNS TXT
    record (a `sc-domain:` property has no other verification method, so losing
    it stops collection instantly and silently), a revoked or expired OAuth
    token, a broken cron, a dead credential. This is the check that matters.

COLLAPSE -- impressions over the last week fell off a cliff against the
    preceding month's daily median.
    Catches the site still being crawled but no longer found: a deindexing, a
    robots.txt mistake, an outage, a botched migration.

Both read the `date` grain only. It is the one that is never anonymized, so it
is the only complete view of the traffic -- see `seo_sync.gsc.GRAINS`.

The logic is a pure function over rows so it can be tested without a database,
a network or a credential. Callers format and deliver the findings themselves;
this module sends nothing: each project delivers findings with its own mailer.
"""
from __future__ import annotations

import datetime as _dt
import statistics
from dataclasses import dataclass
from typing import Sequence

# Search Console finalizes a day roughly 2-3 days after it ends, so a healthy
# archive is always a few days behind. 5 leaves margin for that plus a missed
# cron run without crying wolf, and still catches a real stoppage within a week
# rather than within a quarter.
STALE_DAYS = 5

RECENT_DAYS = 7
BASELINE_DAYS = 28

# Fraction of expected impressions below which the recent window counts as a
# collapse. 0.70 means "less than 30% of normal".
DROP_FRACTION = 0.70

# Below this many expected impressions the comparison is noise, not signal, and
# the check declines to fire. A site averaging one or two impressions a day
# cannot produce a meaningful week-over-month ratio, and a monitor that cries
# wolf on small numbers gets muted, which is worse than not having it.
MIN_BASELINE_IMPRESSIONS = 50


@dataclass(frozen=True)
class Finding:
    code: str  # "stale" | "collapse" | "empty"
    summary: str  # one line, suitable for an email subject
    detail: str  # several lines, suitable for a body


def evaluate(
    rows: Sequence[tuple],
    today: _dt.date,
    *,
    stale_days: int = STALE_DAYS,
    recent_days: int = RECENT_DAYS,
    baseline_days: int = BASELINE_DAYS,
    drop_fraction: float = DROP_FRACTION,
    min_baseline: int = MIN_BASELINE_IMPRESSIONS,
) -> list[Finding]:
    """Findings for `rows` of (date, impressions, clicks) at the `date` grain.

    Returns an empty list when healthy. Ordered most serious first: a stale
    archive makes the collapse check meaningless, so it is reported alone.
    """
    by_date = {r[0]: (int(r[1]), int(r[2])) for r in rows}

    if not by_date:
        return [
            Finding(
                code="empty",
                summary="Search Console archive is empty",
                detail=(
                    "No rows at all for this property.\n"
                    "Either the backfill never ran, or the credential cannot see "
                    "the property."
                ),
            )
        ]

    newest = max(by_date)
    behind = (today - newest).days
    if behind > stale_days:
        return [
            Finding(
                code="stale",
                summary=f"Search Console data has stopped ({behind} days behind)",
                detail=(
                    f"Newest day in the archive: {newest}\n"
                    f"Today:                     {today}\n"
                    f"Behind by:                 {behind} days "
                    f"(alerts above {stale_days})\n\n"
                    "Search Console normally runs 2-3 days behind, so this is not "
                    "that.\n\n"
                    "Most likely causes, in the order worth checking:\n"
                    "  1. DNS TXT verification record removed or changed. A "
                    "sc-domain:\n"
                    "     property has no other verification method, so losing it "
                    "stops\n"
                    "     collection instantly and silently.\n"
                    "  2. The credential stopped working -- revoked OAuth grant, or "
                    "an\n"
                    "     OAuth consent screen left in Testing (7-day refresh "
                    "tokens).\n"
                    "  3. The nightly cron is not running.\n\n"
                    "Whatever Google did not collect is gone for good; it never "
                    "backfills.\n"
                    "Every day this goes unfixed is a day permanently missing."
                ),
            )
        ]

    # Collapse: compare the recent window against the daily median of the window
    # before it. Median, not mean, so one viral day does not set the bar.
    #
    # The window is anchored to `newest`, NOT to today. Search Console always
    # runs 2-3 days behind, so a window ending today is always missing its last
    # few days -- which reads as a 30-40% drop on a perfectly healthy site and
    # would fire this alert every single week until someone muted it.
    recent_start = newest - _dt.timedelta(days=recent_days - 1)
    base_start = recent_start - _dt.timedelta(days=baseline_days)

    recent_impr = sum(
        v[0] for d, v in by_date.items() if recent_start <= d <= newest
    )
    baseline_daily = [
        v[0] for d, v in by_date.items() if base_start <= d < recent_start
    ]
    if not baseline_daily:
        return []

    expected = statistics.median(baseline_daily) * recent_days
    if expected < min_baseline:
        # Too little traffic for the ratio to mean anything. Deliberately silent.
        return []

    if recent_impr < expected * (1 - drop_fraction):
        pct = 100 * (1 - recent_impr / expected) if expected else 100.0
        return [
            Finding(
                code="collapse",
                summary=f"Search impressions down {pct:.0f}% over {recent_days} days",
                detail=(
                    f"Impressions, last {recent_days} days:  {recent_impr}\n"
                    f"Expected from the prior {baseline_days} days: "
                    f"{expected:.0f}\n"
                    f"Down:                             {pct:.0f}% "
                    f"(alerts above {drop_fraction * 100:.0f}%)\n\n"
                    "The archive is still collecting, so this is not a "
                    "verification or\n"
                    "credential problem -- the site is being crawled and is no "
                    "longer\n"
                    "being found. Worth checking:\n"
                    "  - robots.txt and noindex tags\n"
                    "  - Search Console's Pages report for a spike in exclusions\n"
                    "  - whether the site was down or slow during the window\n"
                    "  - a recent deploy that changed URLs without redirects"
                ),
            )
        ]

    return []


def check(
    conn,
    property_: str,
    table: str = "gsc_daily",
    today: _dt.date | None = None,
    **kwargs,
) -> list[Finding]:
    """Read the `date` grain for one property and evaluate it.

    `table` is interpolated into the statement, so it must come from code and
    never from user input. Callers pass a literal.
    """
    today = today or _dt.date.today()
    lookback = today - _dt.timedelta(days=kwargs.get("baseline_days", BASELINE_DAYS) + 60)
    sql = (
        f"SELECT `date`, impressions, clicks FROM `{table}` "
        "WHERE property = %s AND dim_set = 'date' AND `date` >= %s "
        "ORDER BY `date`"
    )
    with conn.cursor() as cur:
        cur.execute(sql, (property_, lookback))
        fetched = cur.fetchall()

    rows = []
    for r in fetched:
        if isinstance(r, dict):
            rows.append((r["date"], r["impressions"], r["clicks"]))
        else:
            rows.append((r[0], r[1], r[2]))
    return evaluate(rows, today, **kwargs)
