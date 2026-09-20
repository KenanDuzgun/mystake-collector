# AGENTS.md

This file defines project rules for AI agents (Claude Code, etc.) working
in this repo. See [`docs/product/PRD.md`](docs/product/PRD.md) for product
requirements.

## 1. Project Summary

A data collection tool that gathers football match metadata and betting
odds from the MyStake betting site and writes them to local storage. The
MVP targets a single bookmaker (MyStake) and a single sport (football).
Accuracy is the top priority — speed and coverage are secondary.

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

The following module layout is the target (folders are created as needed —
do not add abstraction beyond MVP scope):

```
mystake_collector/
    sources/       # MyStake-specific data acquisition (API client, scraper)
    models/        # Data models for match metadata and odds (dataclass/pydantic)
    storage/       # Local file writing (CSV/JSON/SQLite)
    pipeline/       # Flow that converts source data into models and writes to storage
tests/
docs/
    product/
        PRD.md
```

Rules:

- **Source priority**: Data acquisition first investigates whether
  MyStake's API/network requests can be used (e.g. `sources/api.py`). If
  that's not viable, fall back to scraping/crawling (e.g. Playwright, in
  `sources/scraper.py`). Both should produce the same models (`models/`) so
  that the `pipeline` and `storage` layers stay source-agnostic.
- **Flow preference**: Polling is the last resort. If MyStake offers a
  push-based channel (WebSocket/SSE), prefer that.
- **No forced bookmaker-agnostic abstraction**: The MVP only supports
  MyStake. Speculative generalizations like a multi-bookmaker abstraction
  are not added at this stage (YAGNI).
- **Storage**: MVP uses local files (CSV/JSON/SQLite). Integrating with
  messaging systems like Kafka/Pub-Sub is out of MVP scope; the `storage`
  layer should expose a simple interface (e.g. `write(records)`) so a
  different backend can be added later, but that integration is not
  written now.

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
