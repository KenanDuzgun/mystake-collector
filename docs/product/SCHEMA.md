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

`GET /api/sport/getheader/en` returns a hierarchy:

```text
Sports
  -> Regions
    -> Champs
      -> GameSmallItems
```

Each `GameSmallItem` (modeled as `mystake.models.Fixture`):

| Field       | Type   | Status         | Notes                              |
|-------------|--------|----------------|-------------------------------------|
| `GameId`    | int    | PROVEN          | primary key, cross-referenced with prematch/live topics |
| `Sport`     | str    | PROVEN          | ~37 distinct sports observed        |
| `Region`    | str    | STRONG EVIDENCE | e.g. "England"                      |
| `Champ`     | str    | STRONG EVIDENCE | e.g. "Premier League"                |
| `StartTime` | int    | STRONG EVIDENCE | kickoff timestamp                    |
| `t1` / `t2` | varies | STRONG EVIDENCE | team identifiers                    |

`live/headernew/en` is believed to be the live-fixture equivalent of
`getheader/en` (STRONG EVIDENCE, not yet formally diffed field-by-field).

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
- `mk` top-level live field.
- Prematch -> live GameId transition (same id vs. new id at kickoff).
