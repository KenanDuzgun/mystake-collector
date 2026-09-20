# PRD: MyStake Collector

## 1. Summary

A data collection tool that gathers football match metadata and betting
odds from the MyStake betting site accurately and consistently, and writes
them to local storage.

## 2. Problem / Motivation

Football matches and odds on MyStake change over time. There is currently
no reliable, reproducible pipeline to capture these changes. The first goal
is to prove that this pipeline works correctly; scaling and distributed
messaging (Kafka / Google Pub-Sub) will be addressed in later stages.

## 3. Goals

- Collect football match metadata (league, teams, kickoff time, etc.) from
  MyStake.
- Collect betting odds for those matches and record how they change over
  time.
- Prioritize accuracy and consistency of the collected data over speed or
  coverage.
- Write the collected data to local storage (CSV/JSON/SQLite) in the first
  stage.

## 4. Non-goals

- Supporting bookmakers other than MyStake (for now).
- Integrating with messaging infrastructure such as Kafka or Google
  Pub-Sub (planned for later, out of scope for MVP).
- Guaranteeing a real-time SLA (best-effort freshness is the target).
- Supporting sports other than football (may be expanded post-MVP).

## 5. Scope

### 5.1 MVP Scope

- **Sport**: Football only.
- **Data types**:
  - Match/event metadata (league, teams, kickoff time, match status).
  - Betting odds and how they change over time.
- **Data source**: MyStake's API/network requests are tried first (if
  available). If that's not feasible, web scraping/crawling (e.g.
  Playwright) is used instead.
- **Data flow**: Polling is the last resort. Where possible, a push-based
  channel such as WebSocket or SSE (Server-Sent Events) is used to capture
  the freshest data.
- **Storage**: Local file system (CSV/JSON/SQLite — exact choice to be
  decided during technical design).

### 5.2 Post-MVP Scope

- Writing to a messaging system such as Kafka or Google Pub-Sub.
- Adding support for other bookmakers (the architecture isn't forced into
  this today, but the door isn't closed either).
- Adding sports other than football.
- Writing to a central database (Postgres/MySQL, etc.).

## 6. Success Criteria

For the MVP to be considered successful, in priority order:

1. **Accuracy (primary criterion)**: Collected match metadata and odds must
   match the real values on the MyStake site. Incorrect, missing, or
   misleading data is not acceptable.
2. **Reliability**: The system should be able to run for extended periods
   without crashing.
3. **Freshness**: Odds changes should be captured with the lowest possible
   latency (preferring WebSocket/SSE).

## 7. Risks / Open Questions

- Does MyStake expose an official or undocumented API, or will scraping be
  required? (To be determined — API will be tried first.)
- Is there a risk of rate limiting / bot detection / IP bans on MyStake's
  side? This risk is managed via the scraping/ethics rules in `AGENTS.md`.
- The exact precision/format for odds (decimal, fractional, American) has
  not been finalized.
- The exact data schema (JSON/CSV/SQLite fields) will be defined during
  technical design.

## 8. Technical Context (summary)

- Language: Python (>=3.13)
- Package management: `uv`
- Code quality: `ruff` (lint + format), `pytest` (tests)
- See [`AGENTS.md`](../../AGENTS.md) for detailed architecture and
  development rules.
