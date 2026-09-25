# Observed MyStake Payload Schemas

This documents the payload shapes the collector currently parses, as
reverse-engineered so far. Findings are classified as:

```text
PROVEN            observed directly and consistently in decoded traffic
STRONG EVIDENCE    observed repeatedly, not yet exhaustively verified
HYPOTHESIS         plausible but not confirmed
UNKNOWN             field/endpoint exists, semantics not understood
```

See [`docs/handoff/handoff.md`](../handoff/handoff.md) for the full
protocol investigation log this is distilled from.

The typed models in `mystake/models/` intentionally keep every parsed
object's original source dict on a `raw` field, so a field that is not
listed below (or not yet understood) is never dropped — only the
known fields below are lifted into typed attributes.

## 1. Fixture discovery (`getheader/en`) — PROVEN endpoint and schema

`GET https://analytics-sp.googleserv.tech/api/sport/getheader/en`
returns, per a real network capture verified this session:

- **PROVEN**: the HTTP body decodes (`response.json()`) to a `str`,
  not a dict — the endpoint double-JSON-encodes its response. That
  `str` must be `json.loads`-ed again to reach the actual object.
  `parse_prematch_header` does this itself when given a `str`.
- **PROVEN**: the actual object is `{"EN": {"Sports": {...}, ...}}`.
  `Sports`, and each node's `Regions`/`Champs`/`GameSmallItems`, are
  dicts keyed by the entry's own `ID` (as a string), not lists. This
  **corrects** the earlier `{"Sports": [...]}` HYPOTHESIS — the `EN`
  wrapper is a real language-keyed envelope, not just a descriptive
  label.

```text
{"EN": {"Sports": {<id>: {... "Regions": {<id>: {... "Champs":
  {<id>: {... "GameSmallItems": {<id>: {...}}}}}}}}}}
```

Real capture this session: 42 sports, 4,015 `GameSmallItems` (this is
a live sample, not a fixed expected count — see AGENTS.md YAGNI/no
hardcoded-count rule).

Parsed by `mystake.pipeline.prematch_header_parser.parse_prematch_header`,
which walks this hierarchy and builds one `mystake.models.Fixture` per
`GameSmallItem`, tolerating a bare `{"Sports": [...]}` list shape too
(pre-existing tests, resilience).

Each `GameSmallItem`:

| Field       | Type   | Status         | Notes                              |
|-------------|--------|----------------|-------------------------------------|
| `ID`        | int    | PROVEN | fixture identifier. **Corrects Phase 1's `GameId` assumption** — `GameId` is not the observed key on `GameSmallItem` itself (it is, however, the key used by the unrelated `prematch/games` `UpdateList` payload — see section 2 below). `parse_fixture_from_getheader_item` reads `ID` first, falling back to `GameId` only for resilience. |
| `Sport`     | int    | PROVEN (corrects earlier `str` assumption) | the parent `Sport` node's own `ID` (foreign key), **not** a name |
| `Region`    | int    | PROVEN (corrects earlier `str` assumption) | the parent `Region` node's own `ID` (foreign key) |
| `Champ`     | int    | PROVEN (corrects earlier `str` assumption) | the parent `Champ` node's own `ID` (foreign key) |
| `StartTime` | str (ISO 8601) | PROVEN | kickoff time, e.g. `"2026-09-25T02:00:00"` |
| `t1` / `t2` | int    | PROVEN  | team ids — **no team lookup list exists on this endpoint** (unlike `live/headernew/en`'s `Teams`), so these stay unresolved ids |

Parent hierarchy nodes (`Sport`/`Region`/`Champ` objects) each carry
their own `ID` and `Name` fields — **PROVEN** this session (e.g. real
capture: `Sport.ID=23, Sport.Name="Field Hockey"`). Surfaced on
`Fixture` as `sport_id`/`region_id`/`champ_id`. Since a `GameSmallItem`'s
own `Sport`/`Region`/`Champ` are foreign-key ids rather than names, the
parent node's `Name` is always preferred for `Fixture.sport`/`region`/
`champ`; the item's own raw field is used only as a fallback when the
parent node has no `Name` at all.

### 1a. Live fixture discovery (`live/headernew/en`) — PROVEN top-level shape and `Games` join

Reached via the same cache-indirection pipeline as other MQTT-notified
resources (`mystake.sources.cache.client.MystakeCacheClient` +
`mystake.pipeline.cache_decoder.decode_cache_response`), fetched
proactively via `{CACHE_GET_BASE_URL}?key=live/headernew/en` —
**STRONG EVIDENCE by analogy** with the `prematch/games` cache-get URL
pattern (handoff.md section 7); the URL pattern itself is still not
directly/independently observed for this specific key, though a real
fetch through it succeeded this session and returned a well-formed
payload. No MQTT topic exists for live header invalidation (none has
been observed); `mystake.pipeline.live_discovery.LiveFixtureDiscovery.refresh()`
must be called explicitly.

Decoded payload top-level shape (PROVEN, verified against
`tests/fixtures/mystake-live-header-sanitized.json` and a real fetch
this session):

```text
Games          list of live fixtures
Sports         lookup: {ID, Name, kn, N, SportType}
Regions        lookup: {ID, kn, Name, N, SportID}
Championats    lookup: {ID, Name, N, RegionID}
Teams          lookup: {ID, Name}
mk             UNKNOWN — empty in every capture seen so far
```

**PROVEN** (all 84 `Games` entries in the captured fixture, and every
entry in a real live fetch this session, resolve cleanly): a `Games`
entry's `Sport`/`Region`/`Champ`/`Team1`/`Team2` fields are
foreign-key ids into the corresponding top-level lookup list by `ID`:

```text
Games[].Sport  -> Sports[].ID
Games[].Region -> Regions[].ID
Games[].Champ  -> Championats[].ID
Games[].Team1  -> Teams[].ID
Games[].Team2  -> Teams[].ID
```

`mystake.pipeline.live_header_parser.parse_live_header` indexes each
lookup list by `ID` and resolves every `Games` entry's fixture
metadata through it (`mystake.models.fixture.parse_fixture_from_live_game_item`),
surfacing the resolved name on `Fixture.sport`/`region`/`champ`/
`team1`/`team2` and the raw foreign-key id on `Fixture.sport_id`/
`region_id`/`champ_id`/`team1_id`/`team2_id`. If a lookup id has no
matching entry (not observed in any capture so far), the id itself is
kept as the "name" instead of `None` — honest and distinguishable from
a real name, and the fixture is never dropped. `mk` remains UNKNOWN
and unparsed — every capture seen so far (including a real fetch this
session) has it as an empty list, so its schema is still unobserved.

Sample resolved live `Games` entry (documented in `mystake-live-header-sanitized.json`):

```text
GameId       76509222
Sport ID     1          -> Soccer
Region ID    48         -> Chile
Champ ID     106505     -> Copa Chile, Knockout stage
Team1 ID     10963      -> CD Everton Vina del Mar
Team2 ID     10975      -> Universidad de Chile
```

Undocumented per-entry fields (`MatchStatusID`, `ls`, `bgid`, `mc`,
`neut`, `plng`, `ovlng`, `hst`, `tdesc`, `hprs`, `rct1`, `rct2`,
`bgenid`, `MatchTime`, `Score`, `LiveBetStatus`) remain UNKNOWN and are
**not** interpreted — they are preserved verbatim on `Fixture.raw`
only, per AGENTS.md's "do not invent field mappings" rule.

## 1b. Fixture registries and refresh lifecycle

`mystake.registry.fixture_registry.FixtureRegistry` is a generic
in-memory `game_id -> Fixture` store, used for two separate instances
(prematch, live — never shared). `refresh(fixtures)` replaces the
contents and classifies every `game_id` as one of:

```text
ADDED
REMOVED
METADATA_CHANGED
UNCHANGED
```

`REMOVED` means only "this GameId is no longer present in the last
discovery response" — it is **not** inferred to mean the match ended,
and DeleteList-style permanent-deletion semantics are still UNKNOWN
(see section 4). `METADATA_CHANGED` compares `sport`/`region`/`champ`/
`start_time`/`team1`/`team2`/`sport_id`/`region_id`/`champ_id`; `raw`
is intentionally excluded from that comparison (upstream payloads may
reorder/reformat unrelated raw fields between fetches without any of
these being a real metadata change — this is a design choice, not
independently observed).

A discovery service (`PrematchFixtureDiscovery` / `LiveFixtureDiscovery`
in `mystake.pipeline`) preserves the previous registry state whenever
the HTTP/cache request fails, or the response cannot be parsed into a
recognizable `Sports`/`Games` shape — both parsers raise `ValueError`
in that case (rather than silently returning zero fixtures) precisely
so a malformed response is never mistaken for "everything was
removed".

`PrematchHeaderRefreshHandler` subscribes to MQTT `prematch/header`
(PROVEN topic, exact payload semantics UNKNOWN) and triggers
`PrematchFixtureDiscovery.refresh()` on each notification, skipping a
refresh only when the raw/decoded notification value is byte-identical
to the immediately preceding one (a conservative dedup — it does not
assume anything about what the payload actually encodes).

### Phase 2B — executable listener (`watch_prematch_header.py`)

`watch_prematch_header.py` is the long-running executable that wires
`PrematchHeaderRefreshHandler` into a real `MystakeMqttClient`. It was
the one remaining Phase 2 integration gap: the handler existed and was
unit-tested, but nothing subscribed a live MQTT client to
`prematch/header` and dispatched PUBLISH messages to it.

Run it with:

```bash
uv run python watch_prematch_header.py
```

Call chain (all components reused as-is, no parallel implementation):

```text
MystakeMqttClient.connect_with_retry()
  -> MystakeMqttClient.subscribe("prematch/header")   # blocks for SUBACK
  -> PrematchFixtureDiscovery.refresh()                # initial snapshot
  -> loop: MystakeMqttClient.receive_publish()
       -> PrematchHeaderRefreshHandler.handle(message)
            -> (dedup check) -> PrematchFixtureDiscovery.refresh()
                 -> MystakeHttpClient.get_json(getheader/en)
                 -> parse_prematch_header
                 -> FixtureRegistry.refresh() -> FixtureRegistryDiff
```

**Startup ordering** (IMPLEMENTED, unit-tested): the subscription is
established (SUBACK confirmed) *before* the initial `getheader/en`
discovery request is made. Because `MystakeMqttClient` is
synchronous/single-threaded, any PUBLISH that arrives during the
initial discovery is not lost — it is buffered by the underlying
blocking WebSocket/OS socket (and `MystakeMqttClient` additionally
queues any PUBLISH observed while a SUBSCRIBE/UNSUBSCRIBE is in
flight, see `_await_control_packet`) until the listener loop calls
`receive_publish` again. The initial discovery always completes
strictly before the notification loop starts, so it can never be
overwritten by a later, newer refresh.

**Concurrency** (IMPLEMENTED): the listener is single-threaded and
fully synchronous, so there is never more than one `getheader/en`
request in flight, and a notification received while a refresh is
running simply waits in the socket buffer and is processed — via the
existing dedup/coalescing logic in `PrematchHeaderRefreshHandler` —
immediately after. No queue, thread pool, or scheduling library was
introduced.

**Reconnect/resubscribe** (IMPLEMENTED, reused): entirely from
`MystakeMqttClient` — `receive_publish` already reconnects and
resubscribes to all active topics (including `prematch/header`) on a
`ConnectionError`; the listener does not duplicate this logic.

**Failure handling** (IMPLEMENTED, reused): HTTP failures, cache
failures, and parse failures inside `PrematchFixtureDiscovery.refresh()`
are all pre-existing behavior (see above) — the registry is left
untouched and `refresh()` returns `None`, which the listener logs and
continues from.

**REAL-NETWORK VERIFIED** this session, with a real MQTT connection
and real `getheader/en` traffic:

- Initial discovery: 42 sports, 4,019 fixtures.
- A real `prematch/header` PUBLISH burst was observed and processed
  end-to-end: each notification triggered a real cache-URL decode, a
  real `getheader/en` refetch, and a real reconciliation
  (`added=0 removed=0 metadata_changed=0 unchanged=4019` on every
  refresh observed in this session — no fixture changes occurred
  during the observation window). Duplicate/rapid-fire notifications
  within the same burst were correctly deduplicated and skipped a
  redundant HTTP refresh.
- A separate, standalone `discover_fixtures.py` run immediately after
  confirmed idempotency against the same live data:
  `added=0 removed=0 changed=0 unchanged=4019`.

### Graceful shutdown — REAL-NETWORK VERIFIED

**Root cause** of the earlier "NOT VERIFIED / required SIGKILL" gap: no
part of `MystakeMqttClient` had any cooperative shutdown state.
Termination depended entirely on an exception (`KeyboardInterrupt`)
happening to propagate through several layers of blocking I/O - the
TLS-wrapped WebSocket `recv()`, the `websocket` library's own
`selectors`-based retry loop, and the plain `time.sleep()` backoff
delays inside `connect_with_retry`/`_reconnect_and_resubscribe` - with
no explicit check anywhere for "stop reconnecting/retrying, shutdown
was requested" (CONFIRMED by inspection: grepping the client found no
shutdown flag, event, or signal handling at all prior to this fix).
Two concrete, confirmed compounding issues:

- `_reconnect_and_resubscribe`/`connect_with_retry` would resume
  reconnecting/retrying even after a `KeyboardInterrupt` had been
  raised and caught elsewhere, since nothing recorded that shutdown
  had begun.
- `websocket.WebSocket.close()` (used in `serve()`'s `finally`)
  performs a *graceful* close handshake - it sends a CLOSE frame and
  then blocks (default `timeout=3`) waiting for the peer's CLOSE
  frame via `recv_frame()`, swallowing all errors - an additional,
  previously undocumented source of shutdown latency.

Whether OpenSSL's own internal EINTR-retry behavior further delayed
Python's signal-checkpoints under sustained traffic (the working
hypothesis going into this fix) was not directly provable without a
live debugger attached to the blocked syscall; the fix below does not
depend on that hypothesis being correct, since it does not rely on
exception-propagation timing at all.

**Fix** (`mystake/sources/mqtt/client.py`,
`watch_prematch_header.py`): `MystakeMqttClient` now owns a
`threading.Event`-backed shutdown flag and a `ListenerShutdown`
exception.

- `request_shutdown()` (idempotent) sets the flag and forces the raw
  socket closed via `sock.shutdown(socket.SHUT_RDWR)` - not the
  WebSocket-level graceful `close()`, which itself blocks. This makes
  any in-flight or retried `recv()`, at any layer, fail fast with an
  `OSError` instead of blocking again or reconnecting.
- `receive_publish`, `connect_with_retry`, and
  `_reconnect_and_resubscribe` all check the flag at their loop tops
  and raise `ListenerShutdown` instead of retrying/reconnecting; retry
  backoff delays use `Event.wait(seconds)` (interruptible, no busy
  loop) instead of `time.sleep`.
- `watch_prematch_header.py` installs SIGINT *and* SIGTERM handlers
  (`install_shutdown_signal_handlers`) that call `request_shutdown()`
  only - they do not raise directly. This was a deliberate choice: a
  signal landing mid-refresh (inside
  `PrematchFixtureDiscovery.refresh()`'s `except Exception` block,
  which by design preserves registry state on HTTP/parse failures -
  AGENTS.md section 5) would otherwise have its exception silently
  swallowed there. Letting the refresh complete and checking the flag
  at the next natural checkpoint (top of `receive_publish`) avoids
  that hazard entirely. `serve()` catches `ListenerShutdown` alongside
  the existing `KeyboardInterrupt` handling.

**REAL-NETWORK VERIFIED** this session (two separate runs against the
live MQTT broker, `uv run python -u watch_prematch_header.py`):

- Idle shutdown: SIGINT sent ~8s after the last processed
  notification; listener stopped in ~8ms end-to-end (signal received
  -> WebSocket closed -> resources closed), no SIGKILL needed.
- Active-refresh shutdown: SIGINT landed *while* a notification-
  triggered `getheader/en` HTTP refresh was in flight. The refresh
  completed and was reconciled normally
  (`added=0 removed=0 changed=0 unchanged=4028`), and the listener
  then stopped cleanly ~630ms later at the next `receive_publish`
  checkpoint. No reconnection was attempted, no SIGKILL was needed,
  and `ps` confirmed no orphaned process afterwards.
- Both runs: 4,028 fixtures discovered on initial `getheader/en`, real
  `prematch/header` PUBLISH notifications processed, dedup working.

**Remaining limitation**: `MystakeHttpClient.get_json()` has its own
retry/backoff (up to 5 attempts, capped exponential backoff) that is
not shutdown-aware; a shutdown request landing during a prolonged HTTP
retry sequence (e.g. the API returning repeated 503s) could delay
termination by longer than the sub-second latency observed above,
bounded by the HTTP client's own timeout/retry configuration. This was
out of scope for this fix (HTTP client retry logic, not the MQTT
receive loop) and was not exercised in real-network testing since the
API was healthy throughout.

## 2. Prematch authoritative snapshot (`getprematchgamefull`) — PROVEN

`GET /api/prematch/getprematchgamefull/{ctx}/{GameId}` response:

```json
{
  "game": "...JSON string...",
  "price": "[]",
  "disableMarkets": null
}
```

`outer["game"]`, once `json.loads`-ed, is the "game" object modeled as
`mystake.models.Snapshot` (`source="prematch_gamefull"`):

| Field  | Type          | Status | Notes                                   |
|--------|---------------|--------|------------------------------------------|
| `id`   | int           | PROVEN  | GameId                                   |
| `t1`   | int/str       | PROVEN  | team 1 id                                |
| `t2`   | int/str       | PROVEN  | team 2 id                                |
| `st`   | varies        | STRONG EVIDENCE | start time                        |
| `sport`| varies        | STRONG EVIDENCE | sport identifier                  |
| `region`| varies       | STRONG EVIDENCE | region identifier                 |
| `ch`   | varies        | UNKNOWN | championship id; exact semantics unclear |
| `mc`   | int           | STRONG EVIDENCE | market count                      |
| `pc`   | int           | STRONG EVIDENCE | approx. total selection count; sanity check only, not a correctness guarantee |
| `up`   | varies        | STRONG EVIDENCE | update/version marker              |
| `vis`  | bool          | STRONG EVIDENCE | visibility                        |
| `hasstream` | bool     | STRONG EVIDENCE |                                    |
| `ev`   | dict          | PROVEN  | `{market_id: {selection_id: {...}}}`     |

`ev` selection object fields (per `mystake.models.Selection`, parsed
via `parse_prematch_selection`):

| Field  | Type  | Status | Notes                    |
|--------|-------|--------|---------------------------|
| `coef` | float | PROVEN  | selection price/odds      |
| `lock` | bool  | PROVEN  | selection locked flag     |

No `visible` field has been observed on prematch selections (live
snapshots use `visible` instead — see below). This asymmetry is
intentional in the model: `Selection.visible` is `None` for
prematch-sourced selections.

**PROVEN this session** (real `getprematchgamefull` fetches, e.g.
GameId `76513163`): selection entries additionally carry `pos` (int)
and `res` (int, `0` in every real selection observed so far). Neither
is modeled as a typed `Selection` field - their semantics are UNKNOWN
(plausibly a display-order index and a settlement/result marker
respectively, but this is a guess, not evidence) - both are preserved
verbatim on `Selection.raw` only, per AGENTS.md's no-invented-semantics
rule.

**PROVEN this session, market/selection names**: no market or
selection *name* field exists anywhere in a real `getprematchgamefull`
response - markets and selections are identified purely by their
numeric-string ids (e.g. market `448`, `476`, `481`). This was
verified directly against real fetches, not assumed. `Market`/
`Selection` therefore correctly have no `name` field; a caller wanting
human-readable market semantics needs a separate, not-yet-built
mapping layer (explicitly out of scope - AGENTS.md section 3's "no
speculative market mappings" rule).

### `getprematchgameall` — PROVEN partial, NOT modeled as authoritative

`GET /api/prematch/getprematchgameall/{lang}/{ctx}/?games=,{ids}` shares
the outer `game`/`teams` shape but is a **partial** representation:
`gameall markets ⊆ gamefull markets`, and a market/selection missing
from a `gameall` response does not reliably mean "unchanged" or
"removed" (see handoff.md §12). It is not parsed by `mystake/models/`;
only `getprematchgamefull` is treated as authoritative.

Context id `28` (`PREMATCH_CONTEXT_ID`): UNKNOWN semantics, confirmed
NOT to be a sport id.

## 2a. Phase 3: bounded prematch market hydration and updates

IMPLEMENTED, UNIT-TESTED, and REAL-NETWORK VERIFIED this session
(details below). Scope: a small, explicit, bounded set of tracked
GameIds (`PREMATCH_TRACKED_GAMES_MAX = 5`, `mystake/config.py`) - never
the full ~4,000-fixture discovered catalog.

### Decimal odds precision

`mystake.pipeline.prematch_snapshot_hydration._decode_game_object`
decodes the inner `game` JSON string with `json.loads(..., parse_float
=Decimal)`, so `Selection.raw["coef"]` holds the exact decimal
representation from the source JSON text (e.g. `Decimal("5.36")`, not
a `float` rounding of it). `Selection.price` (the typed field) still
coerces this down to `float` via `mystake.models._coerce.coerce_float`
(extended this session to accept `Decimal`), for continuity with the
pre-existing live-snapshot price type and diff comparisons. This is a
deliberate compromise, not a claim that `float` prices are
lossless - `raw` is the accuracy source of truth.

### Snapshot replacement rule

`mystake.registry.game_snapshot_registry.GameSnapshotRegistry` holds
one `TrackedGameState` per tracked GameId. `apply_fetch_result`:

- On a successful fetch, atomically replaces the stored snapshot
  *after* `PrematchSnapshotHydrator.fetch` has already parsed and
  validated it - a snapshot is never partially applied.
- On a failed fetch (`snapshot=None` - HTTP error, malformed outer
  envelope, non-JSON/non-dict `game`), preserves the previously valid
  snapshot untouched and records `last_error`; the returned
  `FetchOutcome.failed=True` and `FetchOutcome.diff=None` distinguish
  this from "no changes" (a real successful fetch that happens to
  match the previous snapshot has `failed=False`, `diff.has_changes=
  False`).
- The very first successful fetch for a GameId has `is_initial=True`
  and `diff=None` (nothing to diff against yet) - this is distinct
  from both a failure and an unchanged refresh.

### Diff semantics

`mystake.pipeline.prematch_snapshot_diff.diff_prematch_snapshots`
compares two `Snapshot` objects by stable `Market.id`/`Selection.id`,
never by list/dict position or display name (no name field exists -
see section 2 above). It distinguishes market added/removed, selection
added/removed, and selection price changed. Market/selection
"availability" or "status" changes are **not** attempted - no such
field's semantics are established (see section 4); this is scoped out
deliberately rather than guessed at.

### Tracked-game hydration bounds and concurrency

`mystake.pipeline.prematch_odds_tracker.PrematchOddsTracker`:

- `max_concurrency` (default `PREMATCH_HYDRATION_MAX_CONCURRENCY = 2`)
  bounds simultaneous `getprematchgamefull` requests via a transient
  `ThreadPoolExecutor` created per hydration batch - no persistent
  per-GameId thread or unbounded queue is created.
- `min_request_interval_seconds` (default
  `PREMATCH_HYDRATION_MIN_REQUEST_INTERVAL_SECONDS = 0.5`) paces
  requests leaving the process (AGENTS.md section 4 rate-limiting
  rule), enforced across all workers via a shared lock.
- A per-GameId in-flight set prevents two overlapping fetches for the
  same GameId; a call that finds one already in flight is coalesced
  into a bounded `_pending` set rather than issuing a duplicate
  request or growing an unbounded queue, and is revalidated exactly
  once more after the in-flight fetch completes.

### MQTT `prematch/games` revalidation

`mystake.pipeline.prematch_games_notification.PrematchGamesRevalidationHandler`
subscribes to the PROVEN `prematch/games` topic (payload semantics
still UNKNOWN - see section 4) and, on each notification:

1. Deduplicates consecutive notifications with an identical decoded
   value (mirroring `PrematchHeaderRefreshHandler`).
2. Attempts to extract GameIds from `UpdateList`/`DeleteList` entries
   *only* to narrow revalidation to a subset of the tracked set when
   the extraction is unambiguous (a real GameId-shaped entry was
   actually found); this is a best-effort narrowing, not a claim that
   the extracted set is a complete or authoritative delta.
3. Falls back to revalidating the entire tracked set - already bounded
   to `PREMATCH_TRACKED_GAMES_MAX` - whenever extraction is
   inconclusive, rather than guessing.
4. Never fetches more than the tracked set regardless of how many
   GameIds a notification references.

Because the executable listener (`watch_prematch_odds.py`) is
single-threaded and synchronous (same architecture as Phase 2B's
`watch_prematch_header.py`), a notification that arrives while a
previous notification's tracked-set revalidation is still running is
never dropped - it is buffered by the OS socket/WebSocket layer until
the next `receive_publish` call, exactly as documented for
`prematch/header` in section 1b. The tracker's in-flight/pending
mechanism additionally guards against the same GameId being fetched
twice concurrently if that assumption is ever relaxed.

### Error and stale-state handling

- HTTP failure, non-2xx status, empty/non-JSON body, or a `game` value
  that is not a JSON object after decoding all result in
  `PrematchSnapshotHydrator.fetch` returning `None` and logging the
  reason - never a fabricated empty/partial snapshot.
- A fetch returning `None` never deletes or blanks a tracked GameId's
  previous valid snapshot; `GameSnapshotRegistry.apply_fetch_result`
  preserves it and records `last_error`.
- "Game temporarily unavailable" has no proven distinct signal from
  "transient HTTP/parse failure" on this endpoint (no such HTTP status
  code or payload marker has been observed) - both currently surface
  identically as a failed fetch that preserves prior state. This is a
  known limitation, not a modeled distinction (see section 4).
- A `getprematchgamefull` response whose `id` does not match the
  requested GameId is logged as a warning (not observed in real
  traffic this session) but still applied - the request URL, not the
  response body, is the source of truth for which tracked GameId a
  fetch belongs to.

### REAL-NETWORK VERIFIED this session

- **Single-game hydration**: a real, freshly discovered Soccer GameId
  (`76516412`) was hydrated via a real `getprematchgamefull` fetch: 4
  real markets, 10 real selections, real decimal odds (e.g. market
  `448` selections priced `2.0`, `4.55`, `2.5`).
- **Multiple tracked games**: 3 real Soccer GameIds tracked
  concurrently (`max_concurrency=2`); each produced an independent,
  correctly-associated snapshot (115 markets / ~970 selections each
  for this particular high-market-count match) with no cross-game
  contamination, confirmed by asserting each registry entry's
  `snapshot.game_id` against its tracked GameId.
- **State preservation**: an immediate second fetch of the same 3
  GameIds produced `diff.has_changes=False` for all three (no false
  changes from re-fetching identical live data).
- **MQTT `prematch/games`**: real `MystakeMqttClient.subscribe`
  returned `SUBACK` accepted; a real burst of `prematch/games`
  PUBLISH notifications was received and processed end-to-end
  (cache-URL decode -> real HTTP fetch of the cache payload ->
  `UpdateList`/`DeleteList` extraction). None of the observed
  notifications during this session's ~30s observation window
  referenced any of the 3 tracked GameIds, so bounded revalidation
  correctly skipped `getprematchgamefull` re-fetches for all of
  them - this is itself evidence the narrowing logic works (it did
  not blindly re-fetch on every global notification), not a gap.
  Duplicate/near-duplicate notifications within the burst were
  correctly deduplicated.
- **Graceful shutdown**: `watch_prematch_odds.py` against the real
  broker - SIGINT sent while a `prematch/games` notification was mid-
  processing; that notification finished being handled normally, and
  the listener then stopped (`ListenerShutdown`, WebSocket closed) in
  under 30ms with no SIGKILL required.

### Phase 3B follow-up — REAL-NETWORK VERIFIED this session

A second real-network session (5 tracked, currently-active, real
Soccer GameIds; ~3.5 minutes of live `prematch/games` traffic; 44 real
notifications processed) additionally confirmed:

- **`UpdateList` entry shape, PROVEN**: a direct real fetch of the
  `prematch/games` cache resource returned, e.g.:
  ```json
  {
    "UpdateList": [
      {"GameId": 76516514, "UpdateTimeStamp": 1790303302408},
      {"GameId": 75435488, "UpdateTimeStamp": 1790303302408},
      {"GameId": 73583827, "UpdateTimeStamp": 1790303302408}
    ],
    "DeleteList": []
  }
  ```
  Each entry is a dict keyed by `GameId` (int) - confirming the key
  name `PrematchGamesRevalidationHandler`'s extraction logic already
  looks for. `UpdateTimeStamp` (int, plausibly epoch milliseconds) is
  a newly observed field; its exact semantics remain UNKNOWN and it is
  not interpreted anywhere. `DeleteList` was empty in every real
  payload observed across both sessions - still no evidence of its
  semantics when non-empty.
- **Bounded fetching under real sustained traffic, PROVEN**: across
  44 real notifications, exactly 5 `getprematchgamefull` requests were
  made in total (the initial hydration only) - zero additional
  requests, because none of the 44 real `UpdateList`/`DeleteList`
  payloads happened to reference any of the 5 tracked GameIds in this
  window. This directly demonstrates the narrowing/bounding logic
  under real load, not just unit tests.
- **Graceful shutdown under real sustained traffic, PROVEN again**:
  SIGINT landed immediately after a real notification was received;
  that notification's cache fetch completed normally, then the
  listener stopped in ~525ms, no SIGKILL required.

### NOT VERIFIED (both sessions)

- **Real observed odds price change** on a tracked GameId: not
  observed in either session's bounded live observation window (~30s,
  then ~3.5 minutes) - no real `UpdateList`/`DeleteList` entry observed
  so far has referenced any tracked GameId, so no tracked snapshot has
  been re-fetched after its initial hydration in real-network testing.
  Reported honestly as **REAL ODDS CHANGE: NOT VERIFIED**, distinct
  from the diff *algorithm*, which is independently deterministically
  tested (`tests/pipeline/test_prematch_snapshot_diff.py`) and was
  exercised against real snapshot data structurally (real fetches,
  identical re-fetch -> no false diff).
- The extraction path's *positive* case (a real notification
  successfully narrowing revalidation to a referenced tracked GameId,
  as opposed to falling back to the full tracked set) is still only
  unit-tested - no real notification observed so far has happened to
  reference a tracked GameId.

## 3. Live snapshot (`live/gamenew/{GameId}` cache payload) — PROVEN

Decoded live cache payload, modeled as `mystake.models.Snapshot`
(`source="live"`):

```json
{
  "Match": { "...": "..." },
  "gmk": [ { "...": "..." } ],
  "mk": [ "..." ],
  "TimeLines": [ { "...": "..." } ]
}
```

`Match` fields consumed by the diff/event layer (`MATCH_FIELDS` in
`mystake/pipeline/live_snapshot_diff.py`), all STRONG EVIDENCE:

```text
GameScore, Score, MatchTime, MatchTimeExtended,
Status, BetStatus, EventStatus, LiveBetStatus, ClockStopped,
CornersTeam1, CornersTeam2,
RedCardsTeam1, RedCardsTeam2,
YellowCardsTeam1, YellowCardsTeam2,
YellowRedCardsTeam1, YellowRedCardsTeam2
```

`gmk` is a flat list of selections (not grouped by market in the
source payload); `mystake.models.parse_live_markets` groups them by
`mid` to produce `Market` objects. Selection fields:

| Field     | Type  | Status | Notes                          |
|-----------|-------|--------|----------------------------------|
| `id`      | int   | PROVEN  | selection id                    |
| `mid`     | int   | PROVEN  | market id                        |
| `v`       | float | PROVEN  | selection price/odds             |
| `visible` | bool  | PROVEN  | selection visibility              |

No `lock` field has been observed in live `gmk` entries (prematch
uses `lock` instead — see above).

**PROVEN this session** (real Phase 4A observation, GameId `75832139`,
Soccer): live `gmk` entries also carry `pid`, `pn`, `posn`, and `h`,
e.g. `{"id": 2876337561, "mid": 616, "pid": 2206, "v": 2.2, "pn":
"under", "posn": 2, "h": 2.5, "visible": true}`. `pn` is plausibly a
human-readable outcome/selection name ("under", presumably "over" for
its sibling) and `h` plausibly the handicap/line value (e.g. `2.5`
goals) — both **HYPOTHESIS only**, not confirmed against any other
signal this session, and **not** modeled on `Selection` (AGENTS.md
section 3's no-speculative-market-mapping rule) — preserved verbatim
on `Selection.raw` only.

**CORRECTS previous UNKNOWN status**: `mk` (top-level, alongside
`gmk`) is **not** always empty. Real Phase 4A observation (GameId
`75832139`) returned 23 real entries, e.g.:

```json
{
  "ID": 616, "Sport": 1, "Name": "Total hometeam",
  "IsHandicap": false, "IsOverUnder": true, "ThreeWayHandicap": false,
  "Category": 31, "cs": [31, 260], "N": 65, "LinkID": 0,
  "LinkIDs": ["1"], "ColumnCount": 2,
  "IsDefaultResultMarket": false, "hasdesc": true
}
```

**STRONG EVIDENCE**: each `mk` entry's `ID` corresponds 1:1 with a
`gmk[].mid` value observed in the same snapshot (all 23 markets in
this capture matched), and `Name` is a real human-readable market
name (e.g. `"Handicap"`, `"Total hometeam"`, `"Correct score AAMS-
logic"`, `"{$competitor1} to win"` — the last showing an
unsubstituted template placeholder, itself worth noting). This
directly resolves part of the "no market/selection name" gap
documented for `getprematchgamefull` in section 2 — **for live
snapshots only**; prematch snapshots were not re-checked this session.
`IsHandicap`/`IsOverUnder`/`ColumnCount`/`Category`/`cs`/`N`/`LinkID`/
`LinkIDs`/`IsDefaultResultMarket`/`hasdesc` remain UNKNOWN semantics
and are not modeled. **Not yet wired into `Market`/`Selection`** (no
`mk`-lookup join was added to `parse_live_markets`/`parse_live_
snapshot` this session — AGENTS.md's "no speculative market mappings
ahead of concrete evidence" rule no longer blocks this for live
snapshots given the evidence above, but implementing the join is
out of Phase 4A's scope and left for a follow-up).

`TimeLines`: list of free-form event dicts (`type`, `team`, `clk`,
...); not schema-constrained, treated as opaque dicts by the diff
layer (`diff.new_timeline_items`).

### MATCH_ENDED criteria — PROVEN (football), STRONG EVIDENCE (general)

```text
Match.Status == 3
Match.BetStatus == 0
Match.EventStatus == 40
```

`Status`/`BetStatus`/`EventStatus` are **not guaranteed to change
together in the same live notification** — MyStake may deliver them
across separate successive updates. `mystake/pipeline/live_snapshot_diff.py`
therefore evaluates the complete current `Match` snapshot against the
complete previous `Match` snapshot (`_is_match_ended_transition`)
rather than requiring all three fields to appear together in a single
diff's changed-field set.

## 4. Still UNKNOWN

- `DeleteList` semantics on `prematch/games` notifications.
- `ch` / context id `28` exact meaning.
- `prematch/markets` topic payload semantics (looks like a
  timestamp/version marker per handoff.md §8).
- `mk` top-level field on `live/headernew/en` (distinct from the
  per-game `live/gamenew/{GameId}` `mk` — see section 3 for the
  latter, now resolved this session): every capture seen so far,
  including a real fetch this session, has it as an empty list.
- `live/gamenew/{GameId}`'s `gmk[].pn`/`h`/`pid`/`posn`, and `mk[]`'s
  `IsHandicap`/`IsOverUnder`/`ColumnCount`/`Category`/`cs`/`N`/
  `LinkID`/`LinkIDs`/`IsDefaultResultMarket`/`hasdesc` (see section 3).
- Prematch -> live GameId transition (same id vs. new id at kickoff).
- `prematch/header` notification payload semantics — decoded but not
  interpreted; used only for byte/value-equality dedup (section 1b).
  **Resolved this session (Phase 2B, section 1b):**
  `PrematchHeaderRefreshHandler` is now wired into a real executable
  listener, `watch_prematch_header.py` — see section 1b for the call
  chain and real-network verification results. `main.py`'s live
  listener and `discover_fixtures.py`'s one-shot HTTP discovery are
  unchanged and remain separate entry points (Phase 2B scope was
  limited to the prematch header refresh wiring).
- `live/headernew/en`'s per-`Games`-entry undocumented fields
  (`MatchStatusID`, `ls`, `bgid`, `mc`, `hst`, `tdesc`, `hprs`, `rct1`,
  `rct2`, `bgenid`, `neut`, `plng`, `ovlng`) — preserved on `raw`,
  semantics not interpreted.
- Whether the `{CACHE_GET_BASE_URL}?key=live/headernew/en` URL pattern
  (inferred by analogy with `prematch/games`) is the *documented*
  mechanism MyStake intends for this key — a real fetch through it
  succeeded and returned well-formed data this session, but the
  pattern itself was reached by analogy, not by observing it in
  MyStake's own client traffic.

**Resolved this session** (see section 1/1a for detail, previously
listed here as UNKNOWN):
`getheader/en`'s double-JSON-encoded body and dict-keyed (not
list-keyed) `{"EN": {"Sports": {...}}}` hierarchy; `GameSmallItem`'s
`Sport`/`Region`/`Champ` being foreign-key ids rather than names;
`live/headernew/en`'s `Games` entry field layout and its join against
`Sports`/`Regions`/`Championats`/`Teams` by `ID`; `Sport`/`Region`/
`Champ` parent-node `ID`/`Name` field names on `getheader/en`.
