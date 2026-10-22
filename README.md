# seo-sync

Shared clients for pulling **Google Search Console**, **GA4** and **DataForSEO** data
into a project's own MySQL database.

## Why this is a library and not a service

Several unrelated projects can use it without being coupled to each other. A shared SEO
*service* would couple them: one schema migration or one rotated credential would take
all of them down at once.

So this package ships **code**, never a running thing. Each project:

- installs its own **pinned** copy into its own environment
- authenticates with its own credential
- writes to its own database
- runs its own schedule

Upgrading one project cannot affect another until that one is upgraded too. The pin is
the whole point — don't install from a branch.

```bash
pip install "seo-sync @ git+https://github.com/exw2004/seo-sync@v0.7.0"
```

How a particular site uses it (its property, credential, schedule, history) belongs in
that site's own repo, not here.

## What's in it

| Module | Purpose |
|---|---|
| `seo_sync.gsc` | Search Analytics client: service-account auth, pagination, retry/backoff, month chunking |
| `seo_sync.store` | Idempotent upserts and range reporting |
| `seo_sync.dataforseo` | Purchased SERP and keyword data, behind a hard spend cap |
| `seo_sync.health` | Detects that an archive has stopped collecting, or that traffic collapsed |
| `seo_sync.migrations/` | Schema templates each project copies into its own migrations directory |

Callers supply credentials, property identifiers and a database connection. This package
knows nothing about any particular site or business, and must stay that way — anything
site-specific belongs in the calling project.

## Auth: service account key or user OAuth token

`GscClient` takes either, detected by file content, so a project switches
between them by swapping the file and changing nothing else.

A Google Workspace organization enforces `iam.disableServiceAccountKeyCreation`
by default, which blocks the service account key download until someone with
Organization Policy Administrator overrides it for that project. OAuth clients
are not covered by that policy. Where a project already has a working
installed-app client, reusing it is the shorter path -- and a user token sees
every property that account owns, so it needs no permission grant in Search
Console either.

## Two behaviours to know before trusting the numbers

**Totals will not match the Search Console UI.** Google withholds rows for privacy, and
the more dimensions requested together, the more it withholds. A `query+page+country+device`
pull sums to fewer clicks than the UI headline. Expected, not fixable, not a bug.

**Recent days are incomplete.** Search Console revises the last ~3 days upward as data
settles. Callers must re-pull a trailing window every run rather than only yesterday; the
upsert makes that idempotent. Syncing only yesterday freezes the first undercounted
version permanently.

## Row identity

`gsc_daily` is keyed on a SHA-256 of the dimension fields, not on a composite unique key
over the columns. A natural key over `property+date+query+page+country+device+search_type`
exceeds InnoDB's 3072-byte index limit under `utf8mb4`, and prefix-truncating `query` and
`page` would silently collapse distinct long URLs into a single row.

Fields are **length-prefixed** before hashing. A plain separator is ambiguous —
`("a|b", "c")` and `("a", "b|c")` serialize identically — which would merge two
distinct rows. `tests/test_logic.py` covers this.

Metrics are deliberately excluded from the hash. That is what lets a revised row upsert
onto its earlier version instead of inserting a duplicate.

## Why the health checks exist

A property can stop collecting for months without anyone noticing, and that data is
gone permanently -- Search Console never backfills. For a `sc-domain:` property,
DNS TXT is the only verification method, so losing
that record stops collection instantly and silently, and the console shows only
a chart trailing to zero that you have to be looking at to see.

`seo_sync.health` returns findings; it sends nothing. Each project delivers them
with its own mailer.

## DataForSEO: every call costs money

Prepaid pay-as-you-go, $50 minimum, every call spends real money. Three rules
shape the module:

- A run declares a budget and **cannot** exceed it. `CostGuard.reserve()`
  refuses the call that would cross it rather than discovering the overspend
  afterwards. Default cap is $1.00.
- `estimate()` prices a plan with **no network call and no account**, so the
  bill is known before it is incurred.
- Every call's actual cost is recorded in `dfs_api_spend`, so local records
  reconcile against the dashboard.

The caps matter because an agent can drive this. A mistyped `depth` or a loop
over a keyword list that turned out to be 10,000 long spends the balance with
no user in the loop.

Stored data is **purchased**, unlike `gsc_daily`. Query the tables, not the API.

## Tests

No network, no database, no credentials required:

```bash
make check    # ruff, every tests/test_*.py, gitleaks
```

## Releases

Projects pin a tag, so every change that projects should get is a PR, then a new tag
(`vX.Y.Z`, matching `version` in `pyproject.toml`). A project moves when it bumps its pin.

## Status

- [x] Search Console (service account key or user OAuth token)
- [x] Archive health checks (stale, collapse)
- [ ] GA4 — set property retention to 14 months first; the default is 2
- [x] DataForSEO
