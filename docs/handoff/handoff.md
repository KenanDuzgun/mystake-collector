# MyStake Collector — Continuation Context / Handoff

## 0. NEW CHAT — READ THIS FIRST

Bu doküman yeni ChatGPT chat session'ına verilecek.

Amaç:

```text
MyStake'ın prematch + live betting data lifecycle'ını
browser bağımsız bir collector ile eksiksiz toplamak.
```

Project:

```text
mystake-collector
```

Environment:

```text
macOS
PyCharm
Python 3.13
uv
Chrome DevTools
```

Project root:

```bash
cd ~/Desktop/Projects/mystake-collector
```

Tests:

```bash
uv run pytest
```

Known baseline:

```text
47 passed
```

Ana çalışma prensibi:

```text
observe
→ experiment
→ prove
→ only then productionize
```

Bulguları mümkün olduğunca:

```text
PROVEN
STRONG EVIDENCE
HYPOTHESIS
UNKNOWN
```

olarak sınıflandır.

Şimdilik:

```text
NO premature Java rewrite
NO DB redesign
NO Redis
NO Kafka/RabbitMQ
NO production refactor before protocol is understood
```

Önce MyStake frontend'in gerçek davranışını çöz.

---

# 1. PROJECT GOAL

Amaç sadece live maçları toplamak değil.

Full lifecycle:

```text
fixture discovery
→ PREMATCH
→ prematch markets / selections / odds
→ prematch updates
→ kickoff
→ LIVE
→ live markets / odds / score
→ MATCH_ENDED
→ final state
→ cleanup
```

Collector browser olmadan bu lifecycle'ı sürdürebilmeli.

---

# 2. CURRENT PROJECT STRUCTURE

Yaklaşık yapı:

```text
mystake-collector/
├── docs/
├── mystake/
│   ├── events/
│   │   ├── mapper.py
│   │   └── models.py
│   ├── pipeline/
│   │   ├── cache_decoder.py
│   │   ├── live_snapshot_diff.py
│   │   └── notification_processor.py
│   ├── sources/
│   │   ├── cache/
│   │   │   └── client.py
│   │   └── mqtt/
│   │       ├── client.py
│   │       ├── message.py
│   │       └── protocol.py
│   └── config.py
├── tests/
├── inspect_prematch_games.py
├── main.py
├── pyproject.toml
└── uv.lock
```

Layer rule:

```text
sources/
→ transport / protocol

pipeline/
→ decoding / transformation / diff

events/
→ semantic events
```

---

# 3. MQTT — PROVEN

WebSocket:

```text
wss://wss-eu-uk1.ws-amazon.com/mqtt
```

Protocol:

```text
MQTT 3.1.1
WebSocket subprotocol = mqtt
clean session = true
keepalive = 60
username = none
password = none
```

Sequence:

```text
CONNECT
→ CONNACK
→ SUBSCRIBE
→ SUBACK
→ PUBLISH
→ PINGREQ / PINGRESP
```

Proven:

```text
reconnect/resubscribe ✅
UNSUBSCRIBE/UNSUBACK ✅
```

Wildcard:

```text
live/gamenew/#
```

rejected:

```text
SUBACK 0x80
```

Exact live topic works:

```text
live/gamenew/{GameId}
```

---

# 4. CACHE INDIRECTION — PROVEN

MQTT PUBLISH çoğu zaman actual JSON taşımıyor.

Observed flow:

```text
MQTT PUBLISH
→ cache URL
→ HTTP GET
→ base64 decode
→ optional gzip
→ JSON decode
```

Implemented and proven.

---

# 5. LIVE SIDE — PROVEN

Live exact topic:

```text
live/gamenew/{GameId}
```

Live snapshot/diff çalışıyor.

Football match-end için güçlü observed pattern:

```text
Status == 3
BetStatus == 0
EventStatus == 40
```

Observed lifecycle:

```text
subscribe
→ live updates
→ match-end
→ final state
→ cleanup
```

---

# 6. PREMATCH MQTT TOPICS — PROVEN

Global topics:

```text
prematch/header
prematch/games
prematch/markets
```

Gerçek MQTT trafiğinde doğrulandı.

GameId-specific prematch MQTT topic bulunmadı.

---

# 7. PREMATCH/GAMES — WHAT WE KNOW

Cache example:

```text
https://wss-eu-uk1.ws-amazon.com/api/cache/get?key=prematch/games
```

Payload shape:

```json
{
  "UpdateList": [
    {
      "GameId": 12345678,
      "UpdateTimeStamp": 1790013300
    }
  ],
  "DeleteList": []
}
```

Critical:

```text
prematch/games
IS NOT
the complete fixture universe
```

Strongest model:

```text
prematch/games
≈ global/broad invalidation/revalidation stream
```

Important:

```text
UpdateList entry
≠ guaranteed underlying state change
```

Observed repeatedly:

```text
prematch/games says GameId X changed
→ browser gamefull fetches X
→ fetched state can be identical to previous state
```

So better semantics:

```text
GameId X should be revalidated/refreshed
```

rather than:

```text
GameId X definitely changed
```

Exact `DeleteList` semantics still UNKNOWN.

Do NOT automatically treat DeleteList as permanent fixture deletion until proven.

---

# 8. PREMATCH/MARKETS

Topic:

```text
prematch/markets
```

Decoded payloads looked like timestamp/version markers:

```text
1789978503
1789979103
```

Difference:

```text
600 sec
```

Strong hypothesis:

```text
prematch/markets
→ timestamp/version/invalidation marker
```

It does not appear to carry actual market state.

Exact semantics unresolved.

---

# 9. PREMATCH HTTP ENDPOINTS

Host:

```text
https://analytics-sp.googleserv.tech
```

Authoritative full snapshot:

```text
/api/prematch/getprematchgamefull/28/{GameId}
```

Partial endpoint:

```text
/api/prematch/getprematchgameall/{LANGUAGE}/28/?games=,{GAME_IDS}
```

Context ID:

```text
28
```

Important:

```text
28 is NOT sport ID
```

Exact meaning UNKNOWN.

---

# 10. GAMEFULL — AUTHORITATIVE

Outer structure resembles:

```json
{
  "game": "...JSON string...",
  "price": "[]",
  "disableMarkets": null
}
```

Decode:

```python
outer = response.json()
game = json.loads(outer["game"])
```

Strongly proven:

```text
gamefull
=
authoritative full state snapshot
```

---

# 11. GAMEALL — PARTIAL

Real comparison:

```text
gameall:
~9 KB
6 markets
81 selections
```

versus:

```text
gamefull:
~782 KB
234 markets
6793 selections
```

Observed:

```text
gameall markets ⊂ gamefull markets
gameall selections ⊂ gamefull selections
```

Therefore:

```text
gameall = partial representation
gamefull = authoritative snapshot
```

---

# 12. GAMEALL IS NOT A COMPLETE LOGICAL DELTA

Earlier merge assumption:

```text
market absent → unchanged
market present → replace market
```

was disproven.

Observed:

```text
market missing from gameall
≠ removed
```

and:

```text
market missing from gameall
≠ unchanged
```

Selection identity can change while selection count stays the same.

Therefore:

```text
gameall
=
partial representation

NOT authoritative snapshot
NOT complete logical delta
```

Do NOT spend more time trying to derive perfect merge semantics yet.

---

# 13. PC CHECK

Observed relation:

```text
gamefull.pc
≈ total selection count
```

Collector currently can do:

```text
local selection count != pc
→ gamefull resync
```

This detects some corruption.

But:

```text
pc match
≠ exact state correctness
```

Observed authoritative divergence with:

```text
same pc
same selection count
different odds/state
```

So `pc` is only a sanity check.

---

# 14. RECONCILIATION EXPERIMENT

Experimental:

```python
RECONCILE_EVERY_GAME_MERGES = 10
```

Flow:

```text
first seen
→ gamefull

known
→ gameall merge

every N merges
→ gamefull reconciliation
```

10-minute soak:

```text
checks  = 11
matches = 1
repairs = 10
```

Approximately:

```text
90.9% repair rate
```

Conclusion:

```text
gameall partial merging does NOT maintain authoritative state
```

Do NOT solve this by:

```text
10 → 5 → 1
```

because that just becomes blind full polling.

This result triggered browser reverse engineering.

---

# 15. PREMATCH/GAMES → GAMEFULL — VERY STRONG

Detail-page experiments showed:

```text
prematch/games contains GameId X
→ shortly afterwards
→ getprematchgamefull/28/X
```

Example for `76433701`:

```text
20:55:05.979 prematch/games
20:55:06.540 gamefull
≈561 ms
```

Another:

```text
20:55:00.245 prematch/games
20:55:00.528 gamefull
≈283 ms
```

For that GameId:

```text
7 gamefull calls
7/7 preceded by same-GameId prematch/games notification
```

Across multiple HARs:

```text
prematch/games
→ relevance decision
→ gamefull
```

is VERY STRONG.

---

# 16. BROWSER DOES NOT FETCH EVERY NOTIFIED GAMEID

Earlier global HAR approximately:

```text
prematch/games entries ≈ 5139
distinct notified GameIds ≈ 2006

gamefull calls = 172
distinct gamefull GameIds = 28
```

Therefore:

```text
prematch/games = global/broad stream
```

Browser does NOT do:

```text
every UpdateList GameId
→ gamefull
```

Instead:

```text
UpdateList
→ is GameId relevant/active for this client?
→ if yes, gamefull
```

This “relevant/active” decision was the main blocker for a while.

---

# 17. SPORT-TAB EXPERIMENTS

Route:

```text
https://mystake.com/en/sportsbook/prematch/upcoming
```

Contains tabs like:

```text
Soccer
Basketball
Tennis
...
```

Selecting different sport does not change route.

Initial hypothesis:

```text
selected sport
→ controls relevant GameId set
```

This was tested.

---

# 18. FIRST SEQUENTIAL SOCCER/BASKETBALL EXPERIMENT

Previous sequential captures had ~13-minute gap, which introduced fixture lifecycle churn.

Because of that, differences could not safely be attributed to selected sport.

Important lesson:

```text
time progression
+
fixture kickoff/expiry
```

was a major confound.

Therefore we performed simultaneous/parallel HAR capture.

---

# 19. PARALLEL HAR EXPERIMENT

Files:

```text
futbol.har
basket.har
```

Captured almost simultaneously.

Common overlap:

```text
272.17 sec
≈ 4m 32s
```

This removed the previous 13-minute time confound.

---

# 20. PARALLEL HAR — NOTIFICATION UNIVERSE

Inside common overlap:

```text
Football-page notifications   = 141 distinct GameIds
Basketball-page notifications = 138 distinct GameIds

Shared                         = 131
Football-only                  = 10
Basketball-only                = 7

Union                          = 148

Jaccard                        = 88.51%
```

Exact `(GameId, UpdateTimeStamp)` comparison:

```text
Football exact notification keys   = 252
Basketball exact notification keys = 236
Shared exact keys                  = 196
Jaccard                            = 67.12%
```

For shared exact notifications:

```text
median arrival difference = 0 ms
p95                       = 5 ms
max                       = 334 ms
```

Almost all arrived virtually simultaneously.

Strong conclusion:

```text
prematch/games
=
shared/global invalidation stream
```

Selected sport is NOT changing the underlying feed in any major way.

---

# 21. PARALLEL HAR — GAMEFULL SET

Inside common overlap:

```text
Football selected   = 20 distinct gamefull GameIds
Basketball selected = 11 distinct gamefull GameIds

Shared               = 10
Football-only        = 10
Basketball-only      = 1

Union                = 21

Jaccard               = 47.62%
```

Shared IDs:

```text
76243885
76295713
76298554
76298562
76397499
76397553
76473350
76473351
76473352
76473355
```

Football-only:

```text
73869181   sport=2   ch=657
74463331   sport=1   ch=107509
76011135   sport=1   ch=1091
76084213   sport=1   ch=583
76243887   sport=2   ch=5779
76397545   sport=2   ch=108287
76397548   sport=2   ch=108287
76442441   sport=1   ch=5068
76473274   sport=1   ch=97089
76473276   sport=1   ch=97089
```

Composition:

```text
6 Soccer
4 Basketball
```

Basketball-only:

```text
76485396   sport=1   ch=69553
```

Important:

```text
Basketball-selected page's only exclusive GameId was Soccer.
```

---

# 22. SAME NOTIFICATION / DIFFERENT FETCH DECISION

Among GameIds notified in BOTH sessions:

```text
Neither fetched           = 110
Both fetched               = 10
Football-only fetched      = 10
Basketball-only fetched     = 1
```

This is critical.

We directly observed:

```text
same GameId
+
same prematch/games notification seen in BOTH browsers
+
one browser calls gamefull
+
other browser does not
```

Therefore a client-local relevance/state decision definitely exists.

However the asymmetric fetches did NOT align cleanly with selected sport.

Football-only:

```text
6 sport=1
4 sport=2
```

Basketball-only:

```text
1 sport=1
0 sport=2
```

Therefore simple model:

```text
selected sport
→ corresponding sport relevance filter
```

does NOT fit the evidence.

---

# 23. SPORT-TAB CONCLUSION

Strong conclusion:

```text
selected sport
≠ primary/exclusive data-subscription selector
```

Strong relevance filter hypothesis does not fit.

Best current classification:

```text
selected sport tab
≈ mostly presentation/rendering state
```

Possible small/partial network influence is not fully excluded.

Do NOT claim:

```text
selected sport has absolutely zero effect
```

because local fetch sets still differed.

But selected sport is clearly NOT the mechanism that defines all relevant fixtures.

---

# 24. GAMEALL OBSERVATION ACROSS REALTIME HARS

Across many realtime HAR captures:

```text
gamefull observed hundreds of times
gameall observed 0 times in realtime refresh chains
```

Correct interpretation:

```text
gameall was NOT observed as part of realtime
prematch/games → refresh chain
```

Do NOT claim:

```text
browser never uses gameall
```

because latest Sports bootstrap HAR finally showed where `gameall` is used.

See below.

---

# 25. NAVIGATION OBSERVED

Important routes:

Initial:

```text
mystake.com
```

Press `Sports`:

```text
https://mystake.com/sportsbook/prematch
```

Press `Upcoming`:

```text
https://mystake.com/en/sportsbook/prematch/upcoming
```

Press `Live`:

```text
https://mystake.com/en/sportsbook/live/eventview/{GameId}
```

Example:

```text
https://mystake.com/en/sportsbook/live/eventview/76432070
```

Press `Sports` again later:

```text
https://mystake.com/en/sportsbook/prematch/top
```

Therefore prematch has multiple views/routes, at least:

```text
/prematch/top
/prematch/upcoming
```

and possibly route/state-specific subsets.

---

# 26. CRITICAL NEW DISCOVERY — SPORTS.HAR

Latest file:

```text
sports.har
```

Capture scenario:

```text
Open mystake.com
→ press Sports
→ browser goes to /sportsbook/prematch
```

This HAR produced the most important recent discovery.

---

# 27. GETHEADER — LIKELY FULL PREMATCH DISCOVERY ENDPOINT

On initial Sports load, browser calls:

```text
GET https://analytics-sp.googleserv.tech/api/sport/getheader/en
```

Response size:

```text
~746 KB
```

Response contains hierarchical fixture discovery data approximately:

```text
EN
└── Sports
    ├── Soccer
    │   └── Regions
    │       └── Champs
    │           └── GameSmallItems
    │               ├── GameId
    │               ├── Sport
    │               ├── Region
    │               ├── Champ
    │               ├── StartTime
    │               ├── t1
    │               └── t2
    │
    ├── Basketball
    ├── Tennis
    ├── Baseball
    ├── Ice Hockey
    ├── Handball
    ├── Formula 1
    └── many others
```

This is the first endpoint found that appears to expose the large prematch fixture universe.

---

# 28. GETHEADER SPORTS / FIXTURE COUNTS

Latest analysis found approximately:

```text
37 different sports
3503 prematch GameIds
```

Example counts:

```text
Soccer             1166
Ice Hockey          492
Basketball          432
Tennis              381
American Football   375
Baseball              98
Rugby                 70
Formula 1             63
Table Tennis          57
Handball              37
MMA                   29
Cricket               24
Volleyball            16
others                ...
```

Total:

```text
3503 GameIds
```

This directly answers a major previous concern:

Collector must NOT be designed only around Soccer/Basketball.

MyStake includes:

```text
Soccer
Basketball
Tennis
Baseball
Ice Hockey
Handball
Formula 1
American Football
Rugby
Table Tennis
MMA
Cricket
Volleyball
...
```

The likely discovery endpoint already covers them in one hierarchy.

---

# 29. CURRENT STRONG HYPOTHESIS — GETHEADER SEMANTICS

Current strongest model:

```text
getheader/en
=
full or near-full prematch discovery tree
```

Conceptually:

```text
Sport
→ Region
→ Champ
→ GameSmallItems
→ GameId
```

This may be the missing bootstrap/discovery mechanism that we were searching for.

Do NOT yet label it 100% “all fixtures forever” until additional validation is performed.

But evidence is very strong.

---

# 30. PREMATCH/HEADER → GETHEADER RELATION

This is another major discovery.

In `sports.har`:

Initial:

```text
GET /api/sport/getheader/en
```

Response contained:

```text
3503 GameIds
```

Then approximately ~1.6 seconds later:

```text
prematch/header
```

notification arrived.

Frontend then called:

```text
GET /api/sport/getheader/en
```

again.

Second response contained:

```text
3502 GameIds
```

Difference:

```text
one GameId disappeared
```

Observed disappearing GameId:

```text
76470869
sport = Tennis
start ≈ 2026-09-21T19:58:00
```

This likely coincided with fixture lifecycle transition/removal.

Therefore current strong hypothesis:

```text
prematch/header
      ↓
invalidate fixture/header universe
      ↓
GET /api/sport/getheader/en
      ↓
refresh discovery tree
```

This is potentially the exact meaning of `prematch/header`.

This needs one focused confirmation experiment before calling it fully PROVEN.

---

# 31. GETPREMATCHTOPGAMES — DIFFERENT PURPOSE

In Sports bootstrap HAR browser also calls:

```text
/api/prematch/getprematchtopgames/en
```

This does NOT appear to be the full fixture universe.

Observed:

```text
~83 GameIds
```

Example distribution:

```text
Sumo         8
Baseball    14
Soccer      16
Ice Hockey   8
Tennis      13
MMA          5
Basketball  13
Formula 1    6
```

Strong interpretation:

```text
getprematchtopgames
=
Top page/display subset
```

not:

```text
global prematch discovery
```

---

# 32. GAMEALL FINALLY HAS A PLAUSIBLE BROWSER ROLE

Immediately after Top/bootstrap data, browser called something like:

```text
getprematchgameall/en/28/?games=,...
```

with a batch of GameIds.

Example observed batch contained around:

```text
16 Soccer GameIds
```

This finally explains why `gameall` existed but was absent in realtime HARs.

Strong model:

```text
BOOTSTRAP / LIST RENDERING
→ getheader / getprematchtopgames
→ choose visible/list GameIds
→ getprematchgameall batch
→ render partial list cards
```

while realtime update path appears to be:

```text
REALTIME
prematch/games
→ relevant GameId
→ getprematchgamefull
→ authoritative state refresh
```

Therefore:

```text
gameall
```

is likely a bootstrap/list-card representation endpoint rather than the authoritative realtime update mechanism.

This is a major conceptual clarification.

---

# 33. CURRENT BEST END-TO-END PREMATCH MODEL

Current strongest architecture model:

```text
STARTUP
   ↓
GET /api/sport/getheader/en
   ↓
discover Sport / Region / Champ / GameId universe
   ↓
optional route-specific subset:
getprematchtopgames
   ↓
getprematchgameall batch
   ↓
render prematch lists
```

Then realtime:

```text
prematch/games
   ↓
GameId invalidation
   ↓
is GameId relevant/active locally?
   ↓
YES
   ↓
getprematchgamefull
   ↓
replace with authoritative state
```

Fixture-universe changes:

```text
prematch/header
   ↓
getheader/en
   ↓
refresh fixture discovery tree
```

This is currently the best evidence-based model.

---

# 34. POSSIBLE CORRECT COLLECTOR DESIGN

Current collector roughly does:

```text
prematch/games
→ gameall
→ partial merge
→ pc check
→ periodic gamefull reconciliation
```

Evidence increasingly says this is the wrong architecture.

Potential replacement:

```text
STARTUP

getheader/en
   ↓
discover all prematch GameIds
   ↓
maintain local fixture registry
```

Then subscribe:

```text
prematch/header
prematch/games
prematch/markets
```

On:

```text
prematch/header
```

potentially:

```text
GET getheader/en
→ compare old/new discovery tree
→ detect additions/removals/metadata changes
```

On:

```text
prematch/games
```

for known/relevant GameIds:

```text
GET gamefull
→ authoritative replace
```

Potential startup market loading strategy still needs design.

Do NOT implement this redesign yet until the remaining protocol questions below are validated.

---

# 35. IMPORTANT BROWSER VS COLLECTOR DISTINCTION

Browser objective:

```text
load/render current page
```

Collector objective:

```text
maintain complete fixture lifecycle across all supported sports
```

Therefore:

```text
browser visible/relevant set
≠ collector relevant set
```

Collector may intentionally track a much larger universe than browser page rendering.

This is why sport-tab filtering is no longer the main issue.

---

# 36. CURRENTLY SOLVED VS UNSOLVED

## PROVEN / VERY STRONG

```text
MQTT transport works
cache indirection works
live exact GameId topics work
live diff works
match-end handling works
```

```text
prematch/header exists
prematch/games exists
prematch/markets exists
```

```text
gamefull = authoritative snapshot
gameall = partial representation
gameall != complete logical delta
pc equality != exact-state equality
```

```text
prematch/games = broad/global invalidation stream
```

```text
relevant same-GameId prematch/games
strongly precedes gamefull
```

```text
browser does NOT gamefull every notified GameId
```

```text
selected Soccer does NOT mean only Soccer fixtures
```

```text
selected Basketball does NOT mean only Basketball fixtures
```

```text
selected sport is NOT primary/exclusive data-subscription selector
```

```text
getheader/en exposes a huge multi-sport prematch fixture tree
```

```text
getprematchtopgames is a much smaller route/display subset
```

```text
gameall is observed during bootstrap/list rendering
```

```text
gameall is NOT observed in realtime prematch/games refresh chain
```

---

# 37. STRONG HYPOTHESES

```text
getheader/en
=
main prematch fixture discovery/bootstrap endpoint
```

```text
prematch/header
→ invalidates getheader data
→ browser refetches getheader
```

```text
getprematchtopgames
=
Top page subset
```

```text
gameall
=
list/bootstrap detail representation
```

```text
gamefull
=
realtime authoritative replacement
```

---

# 38. STILL UNKNOWN

Do NOT claim these as proven yet:

```text
getheader/en is literally 100% of every possible prematch fixture
```

```text
prematch/header always means "refetch getheader"
```

```text
DeleteList exact semantics
```

```text
ch exact semantic meaning
```

```text
28 exact semantic meaning
```

```text
prematch/markets exact semantics
```

```text
how prematch fixture transitions to live GameId/topic
```

```text
whether prematch GameId stays the same when moving live
```

```text
what exact collector relevance policy should be
```

```text
whether all 3500+ fixtures should immediately receive gamefull at startup
```

Important:

Do NOT blindly call `gamefull` for all ~3500 fixtures before designing load/update strategy.

---

# 39. NEXT IMMEDIATE TASK — MOST IMPORTANT

Do NOT go back to Soccer-vs-Basketball tab experiments.

The next experiment should validate:

```text
prematch/header
→ getheader/en
```

relationship.

Goal:

Prove whether `prematch/header` is the fixture-universe invalidation signal.

Suggested controlled experiment:

```text
1. Open Sports / prematch.
2. Network + MQTT logging active.
3. Capture prematch/header notifications.
4. For every prematch/header notification:
   record exact timestamp.
5. Check whether browser calls:
   /api/sport/getheader/en
6. Measure notification → getheader latency.
7. Compare consecutive getheader responses.
8. Determine:
   added GameIds
   removed GameIds
   changed fixture metadata
```

Important output:

```text
header notification timestamp
getheader request timestamp
latency
previous fixture count
new fixture count
added IDs
removed IDs
```

If repeated consistently, classify:

```text
prematch/header
→ getheader revalidation
```

as PROVEN.

---

# 40. AFTER THAT — NEXT EXPERIMENT

Once `prematch/header → getheader` is proven, investigate:

```text
getheader lifecycle semantics
```

Questions:

```text
Does getheader include fixtures days into the future?

Does it include all sports currently shown by MyStake?

Does a fixture disappear at kickoff?

Does it disappear before kickoff?

Does it remain when transitioning to live?

Does its GameId remain the same in live/eventview?

Are new GameIds added through getheader refresh?
```

This is essential for collector fixture lifecycle.

---

# 41. IMPORTANT PREMATCH → LIVE QUESTION

Live route observed:

```text
/en/sportsbook/live/eventview/{GameId}
```

Example:

```text
76432070
```

Live MQTT:

```text
live/gamenew/{GameId}
```

Need to determine:

```text
prematch GameId
→ kickoff
→ live GameId
```

Questions:

```text
same ID?
new ID?
mapping endpoint?
header metadata?
another MQTT topic?
```

This is one of the next major lifecycle questions after discovery is confirmed.

---

# 42. POTENTIAL COLLECTOR ARCHITECTURE AFTER VALIDATION

Possible final design:

```text
BOOTSTRAP
   ↓
getheader
   ↓
FixtureRegistry
   ↓
GameId + Sport + Region + Champ + StartTime + teams
```

Subscriptions:

```text
prematch/header
prematch/games
prematch/markets
```

Handlers:

```text
prematch/header
→ refresh getheader
→ diff registry
→ fixture added / removed / metadata changed
```

```text
prematch/games
→ if GameId in tracked registry
→ fetch gamefull
→ authoritative state replace
```

```text
prematch/markets
→ semantics TBD
```

At kickoff:

```text
prematch fixture
→ live transition detection
→ subscribe live/gamenew/{GameId}
```

At end:

```text
MATCH_ENDED
→ final state
→ cleanup
```

This architecture is NOT yet to be implemented until discovery/header semantics and prematch→live transition are validated.

---

# 43. IMPORTANT PERFORMANCE QUESTION

`getheader` currently exposes roughly:

```text
3500+ fixtures
```

Do NOT assume collector should immediately:

```text
3500 × gamefull
```

That could create unnecessary load.

Need later design for:

```text
initial full market hydration
vs
lazy hydration
vs
time-window tracking
vs
only fixtures with active markets
vs
staged loading
```

But solve protocol semantics first.

---

# 44. CURRENT MENTAL MODEL

The system currently appears to have TWO separate data planes:

## Discovery / Navigation Plane

```text
getheader
getprematchtopgames
gameall
```

Purpose:

```text
what fixtures exist?
how are they grouped?
what should list UI render?
```

## Realtime Authoritative Plane

```text
prematch/games
→ gamefull
```

Purpose:

```text
this known GameId needs revalidation
→ fetch authoritative state
```

And likely:

```text
prematch/header
→ invalidate discovery plane
```

This separation is one of the biggest findings so far.

---

# 45. DO NOT REGRESS TO OLD ASSUMPTIONS

Do NOT go back to:

```text
prematch/games is fixture discovery
```

It is not.

Do NOT go back to:

```text
gameall is realtime delta
```

Evidence says no.

Do NOT assume:

```text
selected sport determines subscription universe
```

Parallel experiment says no.

Do NOT assume:

```text
pc match means local state correct
```

Disproven.

Do NOT assume:

```text
one notification = one fetch
```

Disproven.

Do NOT assume:

```text
DeleteList = immediate permanent delete
```

Unknown.

---

# 46. CURRENT HIGHEST PRIORITY QUESTIONS

In order:

```text
1. Prove prematch/header → getheader revalidation.
```

```text
2. Understand getheader lifecycle:
   additions / removals / kickoff behavior.
```

```text
3. Determine prematch → live GameId transition.
```

```text
4. Decide how collector should hydrate initial market state
   without calling gamefull for thousands blindly.
```

```text
5. Only then redesign collector around authoritative snapshots.
```

---

# 47. MOST IMPORTANT RECENT FILES / EXPERIMENTS

Recent HARs:

```text
futbol.har
basket.har
sports.har
```

Meaning:

```text
futbol.har
→ /prematch/upcoming
→ Soccer selected
→ parallel experiment
```

```text
basket.har
→ /prematch/upcoming
→ Basketball selected
→ parallel experiment
```

```text
sports.har
→ mystake.com
→ Sports click
→ /sportsbook/prematch
→ initial bootstrap/discovery
```

`sports.har` is currently the most important HAR because it revealed:

```text
/api/sport/getheader/en
```

and:

```text
/api/prematch/getprematchtopgames/en
```

plus bootstrap `gameall` behavior.

---

# 48. FIRST ACTION IN NEW CHAT

When continuing:

Do NOT ask what the project is.

Do NOT restart Soccer/Basketball experiments.

Do NOT propose architecture refactor immediately.

Continue from:

```text
getheader/en discovered
+
prematch/header likely invalidates getheader
```

First task:

```text
design or analyze a controlled
prematch/header → getheader experiment
```

If user uploads a new HAR/log:

Analyze it programmatically and specifically extract:

```text
prematch/header notification timestamps
getheader request timestamps
notification → request latency
getheader fixture counts
added GameIds
removed GameIds
changed metadata
```

Then classify result:

```text
PROVEN
STRONG EVIDENCE
UNKNOWN
```

---

# 49. ONE-SENTENCE CURRENT STATUS

If you need to remember only one thing:

```text
We have largely solved prematch realtime refresh
(prematch/games → relevant GameId → authoritative gamefull),
and sports.har has now revealed the likely missing discovery layer:
getheader/en exposes ~3500 fixtures across ~37 sports,
while prematch/header appears to trigger getheader revalidation.
The immediate next goal is to prove that relationship and then
understand fixture lifecycle / prematch→live transition.
```
