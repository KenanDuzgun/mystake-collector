# AGENTS.md

This file defines project rules for AI agents (Claude Code, etc.) working
in this repo. See [`docs/product/PRD.md`](docs/product/PRD.md) for product
requirements.

## 1. Project Summary

A data collection tool that gathers match/fixture metadata and betting
odds from the MyStake betting site (all sports it exposes — see
`getheader/en`, ~37 sports / ~3500 concurrent prematch fixtures) and
writes them to local storage. The MVP targets a single bookmaker
(MyStake) across MyStake's full multi-sport catalog. Accuracy is the
top priority — speed and coverage are secondary.

See [`docs/handoff/handoff.md`](docs/handoff/handoff.md) for the full
protocol reverse-engineering log (MQTT topics, cache indirection,
prematch/live endpoint semantics) and
[`docs/product/SCHEMA.md`](docs/product/SCHEMA.md) for the distilled,
currently-parsed payload schemas.

## 2. Tools and Commands

- **Package management**: [`uv`](https://docs.astral.sh/uv/) is used.
  - Add a dependency: `uv add <package>`
  - Sync the environment: `uv sync`
  - Run a command inside the project environment: `uv run <command>`
- **Lint & format**: [`ruff`](https://docs.astral.sh/ruff/)
  - Lint: `uv run ruff check .`
  - Format: `uv run ruff format .`
- **Tests**: [`pytest`](https://docs.pytest.org/)
  - Run tests: `uv run pytest`

Any agent making code changes must run `ruff check`, `ruff format`, and
`pytest` before committing/opening a PR and ensure they all pass.

## 3. Project Structure and Architecture Rules

Current module layout (folders are created as needed — do not add
abstraction beyond the current milestone's scope):

```
mystake/
    config.py       # MQTT/cache constants
    sources/
        mqtt/        # MQTT-over-WebSocket transport (protocol.py, client.py, message.py)
        cache/       # HTTP cache-indirection client
    pipeline/        # decoding / transformation / diff
        cache_decoder.py          # base64/gzip/JSON cache payload decoding
        notification_processor.py # MQTT PUBLISH -> cache fetch -> decoded payload
        live_snapshot_diff.py     # previous/current live snapshot -> diff
    events/          # semantic events derived from a live diff
        models.py
        mapper.py
    models/          # typed, source-agnostic domain models
        fixture.py    # Fixture (getheader/en GameSmallItem)
        market.py     # Market (+ prematch/live parsers)
        selection.py  # Selection (+ prematch/live parsers)
        snapshot.py   # Snapshot (+ prematch/live parsers)
tests/
docs/
    product/
        PRD.md
        SCHEMA.md
    handoff/
        handoff.md
```

Layer rule (see `docs/handoff/handoff.md` §2):

```text
sources/  -> transport / protocol
pipeline/ -> decoding / transformation / diff
events/   -> semantic events
models/   -> typed domain objects, always retaining the original
             source dict on a `raw` field so unknown/unmodeled
             fields are never lost
```

Rules:

- **Source priority**: Data acquisition first investigates whether
  MyStake's API/network requests can be used (e.g. `sources/mqtt`,
  `sources/cache`, and the prematch/live HTTP discovery endpoints
  documented in `docs/handoff/handoff.md`). Fall back to
  scraping/crawling only if a given data point has no network-API
  path.
- **Flow preference**: Polling is the last resort. MQTT-over-WebSocket
  push notifications (`sources/mqtt`) are the primary channel; HTTP
  calls are used to resolve cache indirection and fetch authoritative
  snapshots, not to poll on a timer.
- **No forced bookmaker-agnostic abstraction**: The MVP only supports
  MyStake. Speculative generalizations like a multi-bookmaker abstraction
  are not added at this stage (YAGNI).
- **No speculative market mappings**: Do not build a semantic
  market/selection name-mapping layer (e.g. "1X2", "Over/Under 2.5")
  ahead of concrete evidence for a given sport/market's payload shape.
  `models/` keeps market/selection identity and raw fields typed;
  human-readable market semantics are a later milestone.
- **Storage**: No database/Redis/Kafka infrastructure yet. Local file
  storage (CSV/JSON/SQLite) is a later milestone; the current
  milestone's scope stops at typed in-memory models and diff/event
  production.

## 4. Scraping / Rate-Limiting and Ethics Rules

- Avoid putting unnecessary load on MyStake's servers. Apply reasonable
  delays (rate limiting) between requests.
- On errors (429, 503, timeout, etc.), retry with exponential backoff; do
  not write unbounded/aggressive retry loops.
- Avoid aggressive parallel request patterns that increase the risk of IP
  bans or account suspension.
- When using scraping/crawling (e.g. Playwright), do not use techniques
  aimed at deliberately deceiving the site (captcha bypass, bot-detection
  evasion). The goal is data collection, not abusing the site.
- Never commit credentials, session cookies, API keys, or other sensitive
  data to the repo; use environment variables or local config files
  excluded via `.gitignore`.

## 5. Data Accuracy (Priority #1)

As stated in the PRD, the top success criterion for this project is
accuracy:

- There must be no silent data loss or conversion errors between raw
  source data and the data mapped into models.
- Ambiguous/missing fields must be logged, not silently dropped.
- Odds format (decimal/fractional/American) and units must be documented
  consistently and normalized to a single standard in code.

## 6. Code Style

- Prefer simple, direct code; do not add abstractions, config options, or
  "might need it later" logic beyond MVP scope.
- Function/class names should convey what they do; comments should only
  explain non-obvious constraints or reasons.
