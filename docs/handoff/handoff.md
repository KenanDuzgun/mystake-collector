
# MyStake Collector — Complete ChatGPT Handoff
## Updated: September 25, 2026 — After Phase 4D

# 0. Instructions for the new ChatGPT conversation

This document transfers the complete working context of the mystake-collector project.

The user should not need to explain the project again.

Respond in Turkish. Technical prompts for Claude Code should generally be in English.

The user is an experienced Java/Spring Boot developer who is learning Python.

When explaining Python, use concise Java comparisons where helpful.

IMPORTANT WORKING PRINCIPLES:

1. Evaluate the latest Claude Code report before suggesting new work.
2. Distinguish actual network observations from unit tests.
3. Do not treat Claude Code reports as independently inspected source code.
4. Do not assume an older ZIP represents the current working tree.
5. Do not repeatedly ask for HAR captures.
6. Reuse existing infrastructure.
7. Do not launch several large phases at once.
8. Prioritize actual Python execution and actual JSON.
9. Do not mark a phase real-network verified based solely on passing tests.
10. Do not ask the user to stop an already-running Claude Code task unnecessarily.

The preferred workflow is:

REAL PYTHON EXECUTION
    ↓
REAL NETWORK OBSERVATION
    ↓
INSPECT ACTUAL JSON
    ↓
UNDERSTAND PROTOCOL
    ↓
IMPLEMENT MINIMAL FUNCTIONALITY
    ↓
RUN AGAIN
    ↓
VERIFY ACTUAL BEHAVIOR

For every phase, answer:

- What can we currently collect?
- What is missing?
- What is the next SINGLE task?

Avoid unnecessarily long Claude Code prompts, speculative architecture, or repetitive test cycles.

---

# 1. Project overview

Project name:

mystake-collector

Local repository:

~/Desktop/Projects/mystake-collector

Objective:

Collect MyStake sportsbook data directly using Python without a browser.

Target capabilities:

- Prematch fixture discovery.
- Live fixture discovery.
- Market and selection discovery.
- Prematch odds.
- Live odds.
- Real-time price changes.
- Score and match-time changes.
- Fixture lifecycle.
- Match-end detection.
- Final-state preservation.
- Subscription cleanup.
- Eventually, reliable prematch-to-live transitions.

The collector communicates through HTTP/cache and MQTT-over-WebSocket.

The initial protocol investigation used Chrome DevTools, HAR captures, and experimental Python scripts.

The core protocol is now understood sufficiently to support real prematch and multi-game live tracking.

New HAR captures should only be requested when there is a concrete technical blocker.

---

# 2. Technology and architecture

Environment:

- macOS.
- Python 3.13.
- uv.
- PyCharm.
- pytest.
- Ruff.

The authoritative project instructions are in:

AGENTS.md

Protocol and schema documentation:

docs/product/SCHEMA.md

Architecture:

sources/
    HTTP, MQTT and cache transport.

pipeline/
    Decode, parsing, hydration, diff, dispatching.

models/
    Typed data models.

registry/
    Fixture and snapshot state.

events/
    Semantic events.

Keep these layers separated.

Do not introduce Java, Spring Boot, PostgreSQL, Redis, Kafka, RabbitMQ, or multi-instance infrastructure at this stage.

The user has considered moving the collector to Java but explicitly decided to continue with Python for now.

---

# 3. Important protocol facts

## Prematch discovery

Endpoint:

GET https://analytics-sp.googleserv.tech/api/sport/getheader/en

Response characteristics:

- May be double-JSON-encoded.
- Contains an EN wrapper.
- Sports, Regions, Champs and GameSmallItems may be dictionary-keyed.
- GameSmallItem Sport/Region/Champ values are foreign-key IDs.
- Parent hierarchy and lookup names must be preserved.

Hierarchy:

Sports
    Regions
        Champs
            GameSmallItems

GameSmallItems[].ID is the prematch GameId.

Previous real observations contained more than 4,000 fixtures.

These counts are historical observations, not fixed expectations.

## Live discovery

Cache resource:

live/headernew/en

Observed root structure:

Games
Sports
Regions
Championats
Teams
mk

Relationships:

Games[].Sport -> Sports[].ID
Games[].Region -> Regions[].ID
Games[].Champ -> Championats[].ID
Games[].Team1 -> Teams[].ID
Games[].Team2 -> Teams[].ID

Important sanitized fixture:

tests/fixtures/mystake-live-header-sanitized.json

Do not rename, overwrite, or unnecessarily regenerate this file.

## MQTT subscriptions

Prematch header:

prematch/header

Prematch game notifications:

prematch/games

Individual live match:

live/gamenew/{GameId}

The wildcard subscription:

live/gamenew/#

was previously rejected with SUBACK 0x80.

Use exact subscriptions for tracked live GameIds.

## Live data flow

MQTT PUBLISH
    ↓
Cache resource URL
    ↓
HTTP GET
    ↓
Base64 decode
    ↓
Optional GZIP decompression
    ↓
JSON
    ↓
Snapshot
    ↓
Diff
    ↓
Semantic events

Reuse the existing transport, decoding, and notification infrastructure.

---

# 4. Completed development history

## Phase 1 — Foundation

COMPLETED.

Implemented:

- MQTT client.
- MQTT protocol packet handling.
- SUBACK/PUBLISH handling.
- HTTP/cache decoding.
- Base64/GZIP.
- Typed models.
- Snapshot/diff infrastructure.
- Initial match-end detection.

Historical test baseline:

69 passed.

## Phase 2 — Fixture Discovery

COMPLETED.

Prematch and live fixture discovery were verified against real network responses.

Prematch discovery returned more than 4,000 fixtures during previous observations.

The sanitized live-header fixture is preserved in tests/fixtures/.

## Phase 2B — MQTT Header Refresh

COMPLETED.

prematch/header is a refresh/revalidation signal, not an authoritative fixture delta.

Existing pipeline:

MQTT notification
    ↓
PrematchHeaderRefreshHandler
    ↓
HTTP header refresh
    ↓
FixtureRegistry reconciliation

Graceful shutdown was fixed and verified.

Historical test baseline:

150 passed.

## Phase 3 — Prematch Market Hydration

COMPLETED.

Authoritative full snapshot endpoint pattern:

getprematchgamefull/28/{GameId}

The earlier gameall partial-merge approach was unreliable.

Production approach:

FULL SNAPSHOT
    ↓
VALIDATION
    ↓
DIFF
    ↓
ATOMIC REPLACEMENT

Do not revert to treating gameall as an authoritative complete delta.

Existing components include:

PrematchSnapshotHydrator
PrematchOddsTracker
PrematchGamesRevalidationHandler
GameSnapshotRegistry
diff_prematch_snapshots()

Concurrency and request pacing are bounded.

Do not blindly hydrate thousands of matches.

## Phase 3B — Real Prematch MQTT Notifications

COMPLETED.

Actual prematch/games payload contained:

UpdateList:
    GameId
    UpdateTimeStamp

DeleteList was observed empty.

Exact DeleteList semantics remain UNKNOWN.

UpdateTimeStamp exact semantics remain UNKNOWN.

Historical test baseline:

198 passed.

## Phase 3C — Real Prematch Odds Changes

COMPLETED.

Instead of selecting random fixtures, actual MQTT UpdateList GameIds were observed first.

A tracked GameId then received real revalidation notifications.

GameId:

76335839

Actual observed price changes:

41.

This was real network traffic, not simulation.

Historical test baseline:

206 passed.

---

# 5. Phase 4A — Real Single Live Match Observation

COMPLETED.

Diagnostic executable:

observe_live_match.py

Actual observed fixture:

GameId=75832139

Municipal vs CD Suchitepequez

Sport: Soccer.

Initial snapshot:

23 markets.
126 selections.

Observation:

18 real MQTT PUBLISH messages.
18 successfully decoded snapshots.
373 real selection price changes.
4 selection removals.

Clock advanced.

No goal or match-end transition occurred during this specific observation.

The captured JSON file was located in an external Claude scratchpad directory:

observed_live_snapshot.json

It was not located inside the project repository.

Do not assume that this historical scratchpad still exists.

Important discovery:

The live payload's mk array contains human-readable market metadata.

The gmk array contains market/selection/price data.

---

# 6. Phase 4B — Bounded Multi-Game Live Tracking

COMPLETED.

New components reported:

mystake/registry/live_game_registry.py
mystake/pipeline/live_odds_dispatcher.py
watch_live_odds.py

Actual command:

uv run python -u watch_live_odds.py --max-games 5 --observe-seconds 90

Five different sports were tracked:

Baseball.
Soccer.
Counter-Strike.
Tennis.
Basketball.

All five exact MQTT subscriptions received accepted SUBACKs.

All five games received independent initial snapshots.

Real price changes were observed in three sports:

Soccer: 645.
Tennis: 337.
Basketball: 93.

Baseball and Counter-Strike received PUBLISH messages but showed no price changes within that specific observation window.

Cross-game state isolation was verified through reported real logs.

Reconnect/resubscribe was unit-tested, not real-network verified.

Historical test baseline:

238 passed.

---

# 7. Phase 4C — Market & Selection Name Enrichment

COMPLETED.

The collector now exposes human-readable metadata.

Actual join:

mk[].ID == gmk[].mid

Verified fields:

mk[].Name:
    Human-readable market name.

gmk[].pn:
    Human-readable selection/outcome name.

gmk[].h:
    Line/handicap value where present.

Actual example:

GameId: 75943936
Fixture: Australia vs Brazil

MarketId: 619
Market: Matchbet and Totals

SelectionId: 2876659054
Selection: home and under

Line: 2.5

Price:
8.00 -> 8.25

Model changes:

Market:
    name
    is_handicap
    column_count

Selection:
    name
    line

New enrichment component:

mystake/pipeline/live_market_enrichment.py

This builds selection metadata from the current snapshot for display.

Unknown fields such as pid, posn and p3 were not assigned speculative semantics.

Market-name placeholders such as {$competitor1} remain unsubstituted.

Prematch market-name enrichment is not implemented by this live-only change.

Actual network observation covered five games across multiple sports.

The report mentioned 100 enriched output blocks, while per-sport price-change counts summed to a larger number. These represent differently reported metrics and should not be conflated without inspecting the logs.

Historical test baseline:

253 passed.

Ruff checks passed on all modified files.

---

# 8. Phase 4D — Live Fixture Lifecycle & Match-End

COMPLETED.

This is the most recently completed phase.

Existing infrastructure already contained:

- A match_ended diff flag.
- MATCH_ENDED semantic event.
- MQTT unsubscribe/UNSUBACK support.

Phase 4D integrated lifecycle management into the existing LiveGameRegistry and LiveOddsDispatcher.

Implemented/reported behavior:

- Lifecycle state management.
- Terminal detection.
- Final snapshot preservation.
- Exactly-once finalization.
- MQTT unsubscribe.
- Active registry cleanup.
- Ignoring late PUBLISH messages.
- Per-game isolation.
- Bounded discovery reconciliation.

## Actual real-network observation

GameId:

76601550

Fixture:

South Africa U20 vs Zambia U20

Sport:

Soccer.

Observed transition:

Status:
1 -> 3

BetStatus:
1 -> 0

EventStatus:
4 -> 40

LiveBetStatus:
True -> False

Final preserved score:

4:2

MatchTime:

90

Actual command:

uv run python -u watch_live_odds.py --game-id 76601550 --game-id 76557634 --observe-seconds 150 --reconcile-interval-seconds 60

Real UNSUBACK was confirmed.

A late PUBLISH was ignored without an additional cache fetch.

The other tracked GameId, 76557634, continued receiving updates and produced 292 real price changes.

This provides real-network evidence of the terminal transition, final snapshot preservation, unsubscribe, and cross-game isolation.

IMPORTANT:

Status=3, BetStatus=0, EventStatus=40 is verified for Soccer.

Do not assume the combination applies to every sport.

A temporary betting suspension must not be interpreted as match completion.

Disappearance from live discovery does not automatically mean the match has ended.

## Remaining Phase 4D limitations

The following are UNIT-TESTED but not REAL-NETWORK VERIFIED:

- A tracked game disappearing from live discovery and entering UNKNOWN handling.
- Reconnecting with a mixture of active and finalized games.
- Skipping finalized games on a real reconnect.

Do not erase these distinctions in future summaries.

Latest reported test baseline:

277 passed.

All touched files passed Ruff.

Previous repository-wide baseline:

27 lint errors.
14 unformatted files.

These were reported as pre-existing.

---

# 9. Important Git observation

During Phase 4D, Claude Code noticed automatic commits named "Handoff" in git log.

Claude reported that it did not manually run git commit.

The actual source of these commits is UNKNOWN.

Potential areas to inspect:

- Git hooks.
- core.hooksPath.
- Claude Code settings/hooks.
- Existing agent integrations.
- Repository automation.

Do not assume the cause.

Do not automatically delete commits, rewrite history, or disable hooks.

The next Phase 5A task includes a read-only investigation of this behavior.

---

# 10. Current known executable files

Known from previous reports:

main.py
discover_fixtures.py
watch_prematch_header.py
watch_prematch_odds.py
observe_and_verify_tracked_odds.py
observe_live_match.py
watch_live_odds.py

The actual current repository is authoritative.

Do not invent filenames or method signatures.

Do not assume an experimental script from an older ZIP represents the current implementation.

---

# 11. What we can currently collect

Based on the latest Claude Code reports:

PREMATCH:

- Fixture discovery.
- Market/selection/odds snapshots.
- Real odds changes.
- MQTT-triggered revalidation.

LIVE:

- Fixture discovery.
- Multiple simultaneous live games.
- Independent per-game state.
- Market names.
- Selection names.
- Line values.
- Real odds changes.
- Scores and match time.
- Market/selection changes.
- Match-end detection.
- Final snapshot preservation.
- MQTT subscription cleanup.

Real multi-game tracking has been verified.

Real Soccer match-end and unsubscribe have been verified.

These statements come from Claude Code reports; ChatGPT has not independently inspected the user's current Mac repository.

---

# 12. Current task — Phase 5A

STATUS:

Prompt prepared.

The user is about to clear Claude Code's context and execute it.

Do not assume Phase 5A is complete.

Phase 5A title:

Real Prematch-to-Live Transition Observation.

PRIMARY QUESTION:

Does a real MyStake match retain its GameId when moving from prematch to live?

We do not know yet.

Do not guess.

Phase 5A must observe an actual upcoming fixture and compare both discovery systems.

Prematch source:

sport/getheader/en

Live source:

live/headernew/en

The task must determine:

1. Actual prematch GameId.
2. Actual live GameId.
3. Whether identity is preserved.
4. Whether explicit cross-references exist.
5. Whether prematch/live discovery overlap.
6. Whether a gap exists during transition.
7. Whether market and selection IDs remain stable.
8. Whether the first live snapshot must be initialized independently.
9. What happens to prematch tracking.
10. What architecture is actually needed for automatic transition.

Prefer Soccer initially.

Select 1–3 real upcoming fixtures.

Observe the actual scheduled kickoff period.

Use bounded real-network execution.

A diagnostic script may be introduced:

observe_prematch_to_live.py

However, the actual repository should determine its implementation and CLI.

Do not create a speculative transition manager before identifying real protocol behavior.

Do not match fixtures authoritatively using only fuzzy team-name matching.

If multiple candidate records match, retain ambiguity.

If no transition occurs in the observation window, report NOT VERIFIED.

Do not fabricate a transition or run indefinitely.

The task must produce a Phase 5A final report.

---

# 13. Phase 5B — Planned, not started

Phase 5B will implement reliable automatic transition based on Phase 5A evidence.

Possible concerns:

- Prematch tracking ownership.
- Live tracking ownership.
- Identity mapping.
- Initial live hydration.
- Duplicate subscription prevention.
- Per-game state preservation.
- Stale in-flight responses.
- Registry handoff.
- Reconnect handling.
- Safe cleanup.

Do not design the exact solution until Phase 5A results are available.

If GameId is preserved, an explicit cross-ID mapping may not be necessary.

If GameId changes, the mapping strategy must be based on real provider evidence.

Do not merge fixtures merely because team names look similar.

---

# 14. Other known unresolved areas

These are not all immediate tasks:

- Cross-sport terminal-state semantics.
- Real reconnect with active/finalized games.
- Real disappearance-from-discovery behavior.
- Live market-name template substitution.
- Prematch market-name enrichment.
- Prematch DeleteList semantics.
- Prematch UpdateTimeStamp exact semantics.
- Long-running soak tests.
- Dynamic all-sports tracking.
- Coverage metrics.
- Persistence.
- Multi-instance orchestration.

Do not combine all these into the next development phase.

---

# 15. How to review future Claude Code reports

When the user returns with the Phase 5A final report:

First provide a concise Turkish executive summary.

Then examine:

- Did Claude run actual Python?
- Which actual fixtures were selected?
- Were their start times verified?
- Was prematch discovery observed?
- Was live discovery observed?
- Was a real transition captured?
- Is the GameId relationship supported by actual data?
- Are market/selection identities stable or different?
- Were there discovery gaps or overlaps?
- Were status and timestamp fields inspected?
- Did Claude avoid speculative identity matching?
- What did the Git Handoff investigation find?
- What passed in pytest and Ruff?
- What is still NOT VERIFIED?

Do not accept a claimed transition without actual supporting observations.

If no real transition was captured, do not ask Claude to rewrite the existing collectors.

Instead, identify the smallest targeted next observation.

If a transition was verified, use its findings to prepare Phase 5B.

Do not automatically start Phase 5B without evaluating the report.

---

# 16. User preferences and collaboration style

The user has approximately 16 years of software development experience, mainly in Java and Spring Boot.

The user is learning Python through this actual project.

When explaining code, comparisons can include:

Python dataclass ≈ Java record/POJO.
Python dict ≈ Java Map.
Python list ≈ Java List.
Python None ≈ Java null.
pytest ≈ JUnit.
Python __init__ ≈ Java constructor.

Do not write unnecessary beginner-level programming lessons.

Focus on practical understanding of the existing Python code.

The user values:

- Short, direct explanations.
- One development phase at a time.
- English Claude Code prompts.
- Real terminal output.
- Actual JSON.
- Actual network validation.
- Explicit unknowns.
- Minimal changes to working code.

The user does not want endless speculative development or inflated test counts without network evidence.

---

# 17. Most important current summary

As of September 25, 2026:

Phase 1: COMPLETE.
Phase 2: COMPLETE.
Phase 2B: COMPLETE.
Phase 3: COMPLETE.
Phase 3B: COMPLETE.
Phase 3C: COMPLETE.
Phase 4A: COMPLETE.
Phase 4B: COMPLETE.
Phase 4C: COMPLETE.
Phase 4D: COMPLETE.

Latest reported tests:

277 passed.

Actual prematch odds changes:

VERIFIED.

Actual multi-game live odds changes:

VERIFIED.

Actual human-readable live market/selection enrichment:

VERIFIED.

Actual Soccer match-end transition and MQTT unsubscribe:

VERIFIED.

Prematch-to-live transition:

NOT YET VERIFIED.

CURRENT TASK:

PHASE 5A — REAL PREMATCH-TO-LIVE TRANSITION OBSERVATION.

Do not restart completed phases.

Wait for the user's Phase 5A Claude Code report, analyze its real evidence, and continue from there.