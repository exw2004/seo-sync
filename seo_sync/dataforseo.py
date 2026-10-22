"""DataForSEO client: the questions Search Console structurally cannot answer.

GSC tells you where you rank in organic results for your own property. It is
blind to the local pack, to competitors, and to keywords you do not already
rank for. That is the whole reason to pay for this.

This is prepaid pay-as-you-go with a $50 minimum, so every call spends real
money. Three things follow, and they shape the whole module:

1.  A run declares a budget and cannot exceed it. `CostGuard` refuses the call
    that would cross the line rather than discovering it afterwards. Default
    caps are deliberately small.

2.  Nothing runs without being estimated first. `estimate()` prices a plan
    with no network call at all, so you always know the bill before it is
    incurred.

3.  Every call's actual cost is recorded. DataForSEO reports `cost` per task;
    it is stored alongside the data in `dfs_api_spend`, so local records can be
    reconciled against the dashboard.

The caps matter more than usual here because an agent can drive this module.
A mistyped `depth`, or a loop over a keyword list that turned out to be 10,000
long, spends the balance with no user in the loop. The guard is the backstop.

Prices below are the published per-call rates as of 2026-09. They are used ONLY
for pre-flight estimates; actual billing always comes from the response, and
`verify_prices()` reports drift against the live account.
"""
from __future__ import annotations

import base64
import datetime as _dt
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

BASE = "https://api.dataforseo.com/v3"

# Published rates, USD. Estimates only -- see module docstring.
PRICE_SERP_LIVE_ADVANCED = 0.002       # per SERP, 10 results
PRICE_SERP_STANDARD = 0.0006           # per SERP, 10 results, ~5 min queue
PRICE_LABS_TASK = 0.012                # per request
PRICE_LABS_ITEM = 0.00012              # per returned row
PRICE_KEYWORDS_SEARCH_VOLUME = 0.06    # per task, up to 1000 keywords

# A run that wants to spend more than this must say so explicitly. Sized so a
# mistake costs the price of a coffee, not the balance.
DEFAULT_BUDGET_USD = 1.00


class BudgetExceeded(RuntimeError):
    """Raised instead of making a call that would cross the run's budget."""


@dataclass
class CostGuard:
    """Tracks spend within one run and refuses to cross the budget.

    Checked BEFORE each call using the published price, then reconciled with
    the cost the response actually reports. Estimating high and reconciling
    down is the safe direction: the guard may stop slightly early, but it can
    never let a run overshoot because a price rose.
    """

    budget_usd: float = DEFAULT_BUDGET_USD
    spent_usd: float = 0.0
    calls: list[dict] = field(default_factory=list)

    def reserve(self, estimated: float, label: str) -> None:
        # Compare with a nanodollar of slack. Repeatedly adding a value like
        # 0.002 accumulates float dust, so 50 calls against a $0.10 budget sums
        # to just over it and the 50th would be refused -- an off-by-one that
        # depends on binary rounding and is impossible to reason about from the
        # outside. The slack is far below the smallest real price.
        if self.spent_usd + estimated > self.budget_usd + 1e-9:
            raise BudgetExceeded(
                f"{label} would cost about ${estimated:.4f}, bringing this run to "
                f"${self.spent_usd + estimated:.4f} against a ${self.budget_usd:.2f} "
                f"budget. Nothing was sent. Raise budget_usd deliberately if this "
                f"is intended."
            )

    def record(self, actual: float, label: str) -> None:
        self.spent_usd += actual
        self.calls.append(
            {"label": label, "cost": actual, "at": _dt.datetime.now(_dt.timezone.utc)}
        )

    @property
    def remaining(self) -> float:
        return max(0.0, self.budget_usd - self.spent_usd)


def estimate(
    *,
    serp_keywords: int = 0,
    serp_live: bool = True,
    labs_requests: int = 0,
    labs_rows_each: int = 1000,
    search_volume_tasks: int = 0,
) -> dict:
    """Price a plan with no network call. Returns a breakdown and a total.

    Use this before funding anything: it answers "what would this cost" without
    an account, a key or a balance.
    """
    serp_rate = PRICE_SERP_LIVE_ADVANCED if serp_live else PRICE_SERP_STANDARD
    serp = serp_keywords * serp_rate
    labs = labs_requests * (PRICE_LABS_TASK + labs_rows_each * PRICE_LABS_ITEM)
    volume = search_volume_tasks * PRICE_KEYWORDS_SEARCH_VOLUME
    return {
        "serp": round(serp, 6),
        "labs": round(labs, 6),
        "search_volume": round(volume, 6),
        "total": round(serp + labs + volume, 6),
    }


class DataForSeoClient:
    """Thin client over the v3 API. Every method spends money except balance()."""

    def __init__(self, login: str, password: str, guard: CostGuard | None = None,
                 timeout: int = 120):
        token = base64.b64encode(f"{login}:{password}".encode()).decode()
        self._auth = f"Basic {token}"
        self.guard = guard or CostGuard()
        self.timeout = timeout

    # -- transport ---------------------------------------------------------

    def _request(self, path: str, payload: list | None = None) -> dict:
        url = f"{BASE}{path}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            url,
            data=data,
            method="POST" if data is not None else "GET",
            headers={
                "Authorization": self._auth,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            raise RuntimeError(f"DataForSEO {path} HTTP {e.code}: {detail}") from None

        if body.get("status_code") != 20000:
            raise RuntimeError(
                f"DataForSEO {path} returned {body.get('status_code')}: "
                f"{body.get('status_message')}"
            )
        return body

    # -- free --------------------------------------------------------------

    def balance(self) -> dict:
        """Account balance and limits. The one call that costs nothing."""
        body = self._request("/appendix/user_data")
        money = body["tasks"][0]["result"][0].get("money", {})
        return {
            "balance": money.get("balance"),
            "total": money.get("total"),
            "limits": money.get("limits", {}),
        }

    # -- paid --------------------------------------------------------------

    def serp_organic(
        self,
        keyword: str,
        *,
        location_code: int | None = None,
        location_coordinate: str | None = None,
        language_code: str = "en",
        device: str = "desktop",
        depth: int = 20,
        live: bool = True,
    ) -> dict:
        """One SERP, parsed. Includes the local pack, which GSC cannot see.

        `location_coordinate` is "lat,lng,zoom" and is what makes local-pack
        results meaningful -- a local business's map-pack position is a function of
        where the searcher is standing.

        `depth` is billed per 10 results, so 20 costs twice 10. Default 20
        because a local business sitting at organic position 11-15 is common
        and invisible at depth 10.
        """
        if location_code is None and location_coordinate is None:
            raise ValueError("one of location_code or location_coordinate is required")

        blocks = max(1, (depth + 9) // 10)
        rate = PRICE_SERP_LIVE_ADVANCED if live else PRICE_SERP_STANDARD
        est = rate * blocks
        label = f"serp:{keyword}"
        self.guard.reserve(est, label)

        task: dict[str, Any] = {
            "keyword": keyword,
            "language_code": language_code,
            "device": device,
            "depth": depth,
        }
        if location_code is not None:
            task["location_code"] = location_code
        if location_coordinate is not None:
            task["location_coordinate"] = location_coordinate

        path = "/serp/google/organic/live/advanced"
        body = self._request(path, [task])
        self.guard.record(float(body.get("cost") or est), label)
        return parse_serp(body, keyword)

    def search_volume(
        self, keywords: Sequence[str], *, location_code: int, language_code: str = "en"
    ) -> list[dict]:
        """Google Ads search volume. One task covers up to 1000 keywords."""
        keywords = list(keywords)
        if not keywords:
            return []
        if len(keywords) > 1000:
            raise ValueError("search_volume takes at most 1000 keywords per task")

        label = f"search_volume:{len(keywords)}kw"
        self.guard.reserve(PRICE_KEYWORDS_SEARCH_VOLUME, label)
        body = self._request(
            "/keywords_data/google_ads/search_volume/live",
            [{
                "keywords": keywords,
                "location_code": location_code,
                "language_code": language_code,
            }],
        )
        self.guard.record(
            float(body.get("cost") or PRICE_KEYWORDS_SEARCH_VOLUME), label
        )
        out = []
        for task in body.get("tasks") or []:
            for row in task.get("result") or []:
                out.append({
                    "keyword": row.get("keyword"),
                    "search_volume": row.get("search_volume"),
                    "cpc": row.get("cpc"),
                    "competition": row.get("competition"),
                })
        return out


    def keyword_suggestions(
        self,
        seed: str,
        *,
        location_code: int,
        language_code: str = "en",
        limit: int = 200,
        min_volume: int = 10,
    ) -> list[dict]:
        """Long-tail phrases CONTAINING the seed, with volumes.

        This is the discovery call: it returns what people actually type around
        a term, which is different from confirming volume for phrases you
        already thought of (that is `search_volume`).

        `limit` is a cost lever, not a preference. Labs bills $0.012 per request
        plus $0.00012 per row, so 1000 rows costs $0.132 and 200 costs $0.036.
        Default low on purpose.

        `min_volume` filters server-side so the cap is spent on rows worth
        having rather than on a tail of zero-volume noise.
        """
        label = f"labs_suggestions:{seed}"
        est = PRICE_LABS_TASK + limit * PRICE_LABS_ITEM
        self.guard.reserve(est, label)

        task: dict[str, Any] = {
            "keyword": seed,
            "location_code": location_code,
            "language_code": language_code,
            "limit": limit,
            "order_by": ["keyword_info.search_volume,desc"],
        }
        if min_volume:
            task["filters"] = [
                ["keyword_info.search_volume", ">=", min_volume]
            ]

        body = self._request(
            "/dataforseo_labs/google/keyword_suggestions/live", [task]
        )
        self.guard.record(float(body.get("cost") or est), label)
        return parse_keyword_items(body)


def parse_keyword_items(body: dict) -> list[dict]:
    """Flatten Labs keyword rows. Raises if a task failed inside an HTTP 200.

    Same reasoning as parse_serp: a failed task has a null result, which
    flattens to an empty list and is indistinguishable from "this seed has no
    suggestions". Recording that as a finding would be inventing data.
    """
    out: list[dict] = []
    for task in body.get("tasks") or []:
        status = task.get("status_code")
        if status is not None and status != 20000:
            raise RuntimeError(
                f"DataForSEO Labs task failed: {status} "
                f"{task.get('status_message')}. Nothing was recorded -- an "
                f"unanswered query must not be stored as an empty answer."
            )
        for result in task.get("result") or []:
            for item in result.get("items") or []:
                info = item.get("keyword_info") or {}
                out.append({
                    "keyword": item.get("keyword"),
                    "search_volume": info.get("search_volume"),
                    "competition": info.get("competition"),
                    "cpc": info.get("cpc"),
                })
    return out


def parse_serp(body: dict, keyword: str) -> dict:
    """Flatten a SERP response into organic results and local-pack entries.

    Kept as a module-level function over the raw response so it can be tested
    against a saved fixture without an account or a balance.
    """
    organic: list[dict] = []
    local_pack: list[dict] = []
    checked_at = _dt.datetime.now(_dt.timezone.utc)

    for task in body.get("tasks") or []:
        # A task can fail inside an HTTP 200 -- an unverified account, an
        # exhausted balance, a bad location. Its `result` is then null, which
        # flattens to zero items and is INDISTINGUISHABLE from a SERP we simply
        # do not appear in. Storing that as "not ranking" writes a fact that was
        # never measured, and it would look exactly like a genuine absence
        # forever after. Fail loudly instead.
        status = task.get("status_code")
        if status is not None and status != 20000:
            raise RuntimeError(
                f"DataForSEO task failed for {keyword!r}: {status} "
                f"{task.get('status_message')}. No ranking was recorded -- an "
                f"unmeasured keyword must not be stored as an absent one."
            )
        for result in task.get("result") or []:
            for item in result.get("items") or []:
                kind = item.get("type")
                if kind == "organic":
                    organic.append({
                        "rank_absolute": item.get("rank_absolute"),
                        "rank_group": item.get("rank_group"),
                        "domain": item.get("domain"),
                        "url": item.get("url"),
                        "title": item.get("title"),
                    })
                elif kind == "local_pack":
                    local_pack.append({
                        "rank_absolute": item.get("rank_absolute"),
                        "rank_group": item.get("rank_group"),
                        "title": item.get("title"),
                        "domain": item.get("domain"),
                        "url": item.get("url"),
                        "rating": (item.get("rating") or {}).get("value"),
                    })

    return {
        "keyword": keyword,
        "checked_at": checked_at,
        "cost": float(body.get("cost") or 0.0),
        "organic": organic,
        "local_pack": local_pack,
    }


def find_domain(results: Iterable[dict], domain: str) -> dict | None:
    """First result matching `domain`, ignoring a leading www.

    Returns None when absent, which is a real and meaningful answer: not
    ranking is different from ranking badly, and the caller should store it as
    such rather than as position 0 or 100.
    """
    want = domain.lower().removeprefix("www.")
    for r in results:
        got = (r.get("domain") or "").lower().removeprefix("www.")
        if got == want:
            return r
    return None
