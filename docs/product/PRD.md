# PRD: MyStake Collector

## 1. Summary

A data collection tool that gathers match/fixture metadata and betting
odds — across all sports MyStake exposes (~37 sports, ~3500 concurrent
prematch fixtures per `getheader/en`; see
[`docs/handoff/handoff.md`](../handoff/handoff.md)) — accurately and
consistently, and writes them to local storage, browser-independently.

## 2. Problem / Motivation

Fixtures and odds on MyStake change over time, across every sport it
lists, not just football. There is currently no reliable, reproducible,
browser-independent pipeline to capture these changes. The first goal
is to prove that this pipeline works correctly; scaling and distributed
messaging (Kafka / Google Pub-Sub) will be addressed in later stages.

## 3. Goals

- Collect fixture/event metadata (sport, region, championship, teams,
  kickoff time, etc.) from MyStake across every sport it exposes.
- Collect betting odds for those fixtures and record how they change over
  time, through the full prematch -> live -> match-ended lifecycle.
- Prioritize accuracy and consistency of the collected data over speed or
  coverage.
- Write the collected data to local storage (CSV/JSON/SQLite) in a later
  stage (out of scope for the current foundation milestone — see
  `AGENTS.md` §3).

## 4. Non-goals

- Supporting bookmakers other than MyStake (for now).
- Integrating with messaging infrastructure such as Kafka or Google
  Pub-Sub (planned for later, out of scope for MVP).
- Database/Redis infrastructure (planned for later, out of scope for the
  current foundation milestone).
- Guaranteeing a real-time SLA (best-effort freshness is the target).
- Speculative semantic market-name mappings (e.g. "1X2") ahead of
  concrete per-sport payload evidence.

## 5. Scope

### 5.1 MVP Scope

- **Sport**: All sports MyStake exposes through fixture discovery
  (`getheader/en` for prematch, `live/headernew/en` for live — currently
  ~37 sports / ~3500 concurrent prematch fixtures). Not football-only.
- **Data types**:
  - Fixture metadata (sport, region, championship, teams, kickoff time).
  - Markets, selections and odds, and how they change over time.
  - Match lifecycle transitions (prematch -> live -> match-ended).
- **Data source**: MyStake's API/network requests (MQTT-over-WebSocket
  push notifications, cache-indirected HTTP fetches, and the prematch/live
  HTTP discovery + snapshot endpoints documented in
  `docs/handoff/handoff.md`). Web scraping/crawling is a fallback only for
  data points with no network-API path.
- **Data flow**: Polling is the last resort. The primary channel is the
  MQTT-over-WebSocket push notification stream MyStake's own frontend
  uses; HTTP is used to resolve cache indirection and fetch authoritative
  snapshots, not to poll on a timer.
- **Storage**: Local file system (CSV/JSON/SQLite — exact choice to be
  decided during technical design; not part of the current foundation
  milestone).

### 5.2 Post-MVP Scope

- Writing to a messaging system such as Kafka or Google Pub-Sub.
- Adding support for other bookmakers (the architecture isn't forced into
  this today, but the door isn't closed either).
- Writing to a central database (Postgres/MySQL, etc.).
- Semantic market/selection name mappings per sport.

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
