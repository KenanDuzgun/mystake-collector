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

### `getprematchgameall` — PROVEN partial, NOT modeled as authoritative

`GET /api/prematch/getprematchgameall/{lang}/{ctx}/?games=,{ids}` shares
the outer `game`/`teams` shape but is a **partial** representation:
`gameall markets ⊆ gamefull markets`, and a market/selection missing
from a `gameall` response does not reliably mean "unchanged" or
"removed" (see handoff.md §12). It is not parsed by `mystake/models/`;
only `getprematchgamefull` is treated as authoritative.

Context id `28` (`PREMATCH_CONTEXT_ID`): UNKNOWN semantics, confirmed
NOT to be a sport id.

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

`mk` (top-level, alongside `gmk`): UNKNOWN structure, not currently
parsed.

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
- `mk` top-level field (on `live/gamenew/{GameId}`, and on
  `live/headernew/en` — every capture seen so far, including a real
  fetch this session, has it as an empty list).
- Prematch -> live GameId transition (same id vs. new id at kickoff).
- `prematch/header` notification payload semantics — decoded but not
  interpreted; used only for byte/value-equality dedup (section 1b).
  Additionally: `PrematchHeaderRefreshHandler` (section 1b) is
  unit-tested against synthetic MQTT messages, but is **not wired into
  any executable entry point** — no code subscribes an
  `MqttClient` to `prematch/header` and dispatches to it. `main.py`'s
  long-running MQTT loop only subscribes to a single hardcoded
  `live/gamenew/{id}` topic; `discover_fixtures.py` refreshes prematch
  fixtures via a one-shot HTTP call, not MQTT. Wiring this up would
  mean adding a new long-running listener design, out of scope for
  this phase per AGENTS.md/task scope discipline.
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
