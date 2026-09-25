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

## 1. Fixture discovery (`getheader/en`) — PROVEN endpoint, STRONG EVIDENCE schema

`GET https://analytics-sp.googleserv.tech/api/sport/getheader/en`
returns a hierarchy directly as JSON (no cache indirection, no base64
wrapping):

```text
Sports
  -> Regions
    -> Champs
      -> GameSmallItems
```

Parsed by `mystake.pipeline.prematch_header_parser.parse_prematch_header`,
which walks this hierarchy and builds one `mystake.models.Fixture` per
`GameSmallItem`.

Each `GameSmallItem`:

| Field       | Type   | Status         | Notes                              |
|-------------|--------|----------------|-------------------------------------|
| `ID`        | int    | PROVEN (Phase 2 session) | fixture identifier. **Corrects Phase 1's `GameId` assumption** — `GameId` is not the observed key on `GameSmallItem` itself (it is, however, the key used by the unrelated `prematch/games` `UpdateList` payload — see section 2 below). `parse_fixture_from_getheader_item` reads `ID` first, falling back to `GameId` only for resilience. |
| `Sport`     | str    | PROVEN          | ~37 distinct sports observed        |
| `Region`    | str    | STRONG EVIDENCE | e.g. "England"                      |
| `Champ`     | str    | STRONG EVIDENCE | e.g. "Premier League"                |
| `StartTime` | int    | STRONG EVIDENCE | kickoff timestamp                    |
| `t1` / `t2` | varies | STRONG EVIDENCE | team identifiers                    |

Parent hierarchy nodes (`Sport`, `Region`, `Champ` objects under
`Sports`/`Regions`/`Champs`) are each assumed to carry their own `ID`
and `Name` fields (e.g. a `Sport` node's own `ID`/`Name`, distinct from
the `Sport` string embedded on each `GameSmallItem`). This is
**HYPOTHESIS**: inferred by analogy with the now-proven
`GameSmallItem.ID` convention, not independently verified. Surfaced on
`Fixture` as `sport_id` / `region_id` / `champ_id`. If a
`GameSmallItem` omits its own `Sport`/`Region`/`Champ` name, the
parent node's `Name` is used as a fallback.

Top-level response envelope: assumed to be `{"Sports": [...]}`
directly (the `EN` label in the original hierarchy sketch is
descriptive of the language-scoped endpoint, not an observed JSON
key). `parse_prematch_header` tolerates one extra level of dict
nesting defensively, but this has not been observed as necessary.

### 1a. Live fixture discovery (`live/headernew/en`) — STRONG EVIDENCE top-level shape, UNKNOWN inner schema

Reached via the same cache-indirection pipeline as other MQTT-notified
resources (`mystake.sources.cache.client.MystakeCacheClient` +
`mystake.pipeline.cache_decoder.decode_cache_response`), fetched
proactively via `{CACHE_GET_BASE_URL}?key=live/headernew/en` —
**STRONG EVIDENCE by analogy** with the `prematch/games` cache-get URL
pattern (handoff.md section 7), not directly observed for this
specific key. No MQTT topic exists for live header invalidation
(none has been observed); `mystake.pipeline.live_discovery.LiveFixtureDiscovery.refresh()`
must be called explicitly.

Decoded payload top-level shape (STRONG EVIDENCE, this session):

```text
Games
Sports
Regions
Championats
Teams
mk
```

This is **not** the same nested shape as `getheader/en` — it appears
to be a flatter/normalized structure (a `Games` list plus separate
`Sports`/`Regions`/`Championats`/`Teams` lookup lists), though this is
not confirmed.

`mystake.pipeline.live_header_parser.parse_live_header` parses only
`Games`, one `Fixture` per entry (`source="live_headernew"`). Per
AGENTS.md's "do not invent field mappings" rule, it does **not**
attempt to cross-reference `Games` entries against
`Sports`/`Regions`/`Championats`/`Teams` by id — the exact
cross-reference key names are UNKNOWN and no captured payload sample
has been verified. Each `Games` entry is parsed with the same
field-extraction convention already proven for `GameSmallItem` (`ID`
for the identifier, `Sport`/`Region`/`Champ`/`t1`/`t2` if present) as
a HYPOTHESIS best effort; any field not present on the entry comes
back `None` rather than being guessed, and the complete raw entry is
always preserved on `Fixture.raw`. `mk` is UNKNOWN and unparsed (same
status as the `mk` field on individual live game snapshots — see
section 3 below).

**Still UNKNOWN**: whether `Games` entries actually carry
`Sport`/`Region`/`Champ`/`t1`/`t2` directly, or whether resolving
fixture metadata requires the `Sports`/`Regions`/`Championats`/`Teams`
join. This needs a captured `live/headernew/en` payload sample to
resolve. Until then, live `Fixture.sport`/`region`/`champ` may come
back `None` in real traffic even though the fixture (`game_id`) itself
is discovered correctly.

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
- `mk` top-level live field (both on `live/gamenew/{GameId}` and on
  `live/headernew/en`).
- Prematch -> live GameId transition (same id vs. new id at kickoff).
- `prematch/header` notification payload semantics — decoded but not
  interpreted; used only for byte/value-equality dedup (section 1b).
- `live/headernew/en`'s `Games` entry field layout, and whether/how it
  cross-references the `Sports`/`Regions`/`Championats`/`Teams` lookup
  lists in the same payload (section 1a) — no live payload has been
  captured/verified this session, so live fixture `sport`/`region`/
  `champ` resolution is not yet implemented, only hypothesized.
- Whether the `{CACHE_GET_BASE_URL}?key=live/headernew/en` URL pattern
  (inferred by analogy with `prematch/games`) is actually correct for
  this key — not directly observed.
- `Sport`/`Region`/`Champ` parent-node `ID`/`Name` field names on
  `getheader/en` (section 1) — inferred by analogy with
  `GameSmallItem.ID`, not independently verified.
