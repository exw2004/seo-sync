"""Google Search Console Search Analytics client.

Pulls rows from the searchAnalytics.query endpoint and normalizes them into the
shape `seo_sync.store.upsert_gsc_rows` expects.

Auth accepts either credential file Google produces, detected by content:

  service account key    {"type": "service_account", ...}
      The service account's email must also be added as a user on the Search
      Console property (Settings -> Users and permissions); "Restricted" is
      enough. Creating the key does not by itself grant access to any property.

  user OAuth token       {"refresh_token": ..., "client_id": ...}
      Written by InstalledAppFlow during a one-time consent. Acts as the human
      who consented, so it already sees every property that account owns and
      needs no permission grant at all.

The OAuth form exists because a Google Workspace organization enforces
`iam.disableServiceAccountKeyCreation` by default, which blocks the service
account key download until someone with Organization Policy Administrator
overrides it. OAuth clients are not covered by that policy, so where a project
already has a working installed-app client, reusing it is the shorter path.

Property identifiers take either Search Console form:
    sc-domain:example.com        (Domain property)
    https://example.com/         (URL-prefix property, trailing slash matters)

Two behaviours worth knowing before you trust the numbers:

1.  Google withholds rows for privacy. The more dimensions you request together,
    the more rows are withheld, so a query+page+country+device pull sums to fewer
    clicks than the Search Console UI headline. That is expected and not fixable.

2.  Recent days are incomplete and get revised upward for roughly three days.
    Callers should re-pull a trailing window every run rather than only yesterday;
    the upsert makes that idempotent.
"""
from __future__ import annotations

import datetime as _dt
import json
import random
import time
from typing import Iterable, Iterator, Sequence

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as UserCredentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

# The API caps a single response at 25,000 rows; we page with startRow.
ROW_LIMIT = 25_000

# Search Console retains 16 months. Ask for more and the API silently clamps,
# which is harmless but makes logs misleading, so we clamp explicitly.
RETENTION_DAYS = 480

# Google anonymizes rows that too few users generated, and the finer the slice
# the more it withholds. On a low-traffic site the effect is total: over 90 days,
# "date" alone can return every click while "date+query+page+country+device"
# returns a handful of rows and ZERO clicks. Archiving only the fine grain would
# store almost nothing.
#
# So we pull several grains and store them side by side, discriminated by
# `dim_set`. Coarse grains carry the totals you can trust; fine grains carry
# whatever detail survives anonymization. Never SUM across grains -- each is a
# complete view of the same traffic at a different resolution.
GRAINS: dict[str, tuple[str, ...]] = {
    "date": ("date",),
    "date+country+device": ("date", "country", "device"),
    "date+page": ("date", "page"),
    "date+query": ("date", "query"),
    "date+query+page": ("date", "query", "page"),
}

# Ordered coarse -> fine, which is also most-complete -> least-complete.
DEFAULT_GRAINS = tuple(GRAINS)

_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 6


def load_credentials(path: str):
    """Build credentials from a service account key or a user OAuth token file.

    Detected by content rather than by filename or a flag, so a project can
    switch between the two by swapping the file and changing nothing else.

    A user token is refreshed in memory by the transport on use. The refresh
    token itself does not rotate, so the file never needs rewriting and can stay
    read-only on the server.
    """
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)

    if data.get("type") == "service_account":
        return service_account.Credentials.from_service_account_file(path, scopes=SCOPES)

    if data.get("refresh_token"):
        return UserCredentials.from_authorized_user_file(path, SCOPES)

    raise ValueError(
        f"{path} is neither a service account key (no type=service_account) nor a "
        f"user OAuth token (no refresh_token). Keys present: {sorted(data)}"
    )


class GscClient:
    """Read-only Search Analytics client for a single property."""

    def __init__(self, credentials_path: str, site_url: str):
        creds = load_credentials(credentials_path)
        # cache_discovery=False avoids a noisy warning under a non-writable cwd,
        # which is how this runs under cron.
        self._service = build(
            "searchconsole", "v1", credentials=creds, cache_discovery=False
        )
        self.site_url = site_url

    # -- low level ---------------------------------------------------------

    def _query(self, body: dict) -> dict:
        """One API call, with backoff on the transient statuses."""
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                return (
                    self._service.searchanalytics()
                    .query(siteUrl=self.site_url, body=body)
                    .execute()
                )
            except HttpError as e:
                status = getattr(e.resp, "status", None)
                if status not in _RETRY_STATUS or attempt == _MAX_ATTEMPTS:
                    raise
                # Exponential with jitter. Quota errors in particular want a real
                # pause rather than a tight retry.
                delay = min(2 ** attempt, 60) + random.uniform(0, 1)
                time.sleep(delay)
        raise RuntimeError("unreachable")

    # -- public ------------------------------------------------------------

    def fetch(
        self,
        start_date: _dt.date,
        end_date: _dt.date,
        grain: str = "date",
        search_type: str = "web",
        data_state: str = "final",
    ) -> Iterator[dict]:
        """Yield normalized rows for one grain over a date range, paging until done.

        `data_state="final"` excludes days Google has not finished processing.
        Pass "all" to include fresh partial data, but only alongside a trailing
        re-pull, or the archive keeps undercounted rows forever.
        """
        if grain not in GRAINS:
            raise ValueError(f"unknown grain {grain!r}; expected one of {sorted(GRAINS)}")
        dimensions = list(GRAINS[grain])
        start_row = 0
        while True:
            body = {
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
                "dimensions": dimensions,
                "type": search_type,
                "dataState": data_state,
                "rowLimit": ROW_LIMIT,
                "startRow": start_row,
            }
            resp = self._query(body)
            rows = resp.get("rows", [])
            for row in rows:
                yield _normalize(row, dimensions, self.site_url, search_type, grain)
            if len(rows) < ROW_LIMIT:
                return
            start_row += ROW_LIMIT


def _normalize(
    row: dict, dimensions: Sequence[str], site_url: str, search_type: str, grain: str
) -> dict:
    """API row -> flat dict with every column present, unused dimensions blank."""
    keys = dict(zip(dimensions, row.get("keys", []), strict=False))  # missing keys stay blank
    return {
        "property": site_url,
        "dim_set": grain,
        "date": keys.get("date", ""),
        "query": keys.get("query", ""),
        "page": keys.get("page", ""),
        "country": keys.get("country", ""),
        "device": keys.get("device", ""),
        "search_type": search_type,
        "clicks": int(row.get("clicks", 0)),
        "impressions": int(row.get("impressions", 0)),
        "ctr": float(row.get("ctr", 0.0)),
        "position": float(row.get("position", 0.0)),
    }


def month_chunks(
    start_date: _dt.date, end_date: _dt.date
) -> Iterable[tuple[_dt.date, _dt.date]]:
    """Split a range into calendar-month spans, oldest first.

    The backfill commits after each chunk, so an interrupted 16-month run keeps
    the months it already wrote and the next run picks up from there.
    """
    cur = start_date
    while cur <= end_date:
        if cur.month == 12:
            nxt = _dt.date(cur.year + 1, 1, 1)
        else:
            nxt = _dt.date(cur.year, cur.month + 1, 1)
        yield cur, min(nxt - _dt.timedelta(days=1), end_date)
        cur = nxt


def retention_floor(today: _dt.date | None = None) -> _dt.date:
    """Oldest date Search Console will still serve."""
    today = today or _dt.date.today()
    return today - _dt.timedelta(days=RETENTION_DAYS)
