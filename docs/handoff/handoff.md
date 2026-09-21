# MyStake Collector — Continuation Context

## 0. YENİ CHAT'TEKİ İLK GÖREV — ÇOK ÖNEMLİ

Bu context ile birlikte kullanıcı sana **iki HAR dosyası yükleyecek**:

```text
soccer.har
basketball.har
```

Dosyaların anlamı:

```text
soccer.har
=
https://mystake.com/en/sportsbook/prematch/upcoming
Soccer tab selected
Network cleared AFTER Soccer selected
Preserve Log ON
~5 dakika observation
no interaction
```

```text
basketball.har
=
https://mystake.com/en/sportsbook/prematch/upcoming
Basketball tab selected
Network cleared AFTER Basketball selected
Preserve Log ON
~5 dakika observation
no interaction
```

URL tab değiştirildiğinde değişmiyor.

Bu BEKLENEN davranış.

Muhtemelen selected sport frontend/client state içinde tutuluyor.

Yeni chat'te İLK İŞ:

```text
soccer.har
VS
basketball.har
```

karşılaştırması yapmak.

Başka deney önermeden önce iki HAR'ı mümkün olduğunca ayrıntılı analiz et.

---

# 1. CURRENT RESEARCH QUESTION

Şu anda çözmeye çalıştığımız ana soru:

```text
How does MyStake frontend determine
which prematch GameIds are relevant/active?
```

Özellikle mevcut deney:

```text
Does switching:

Soccer
→ Basketball

change the frontend's relevant fixture set?
```

Model:

```text
Soccer selected:
relevant GameId set = A

Basketball selected:
relevant GameId set = B
```

Analiz edeceğimiz:

```text
A ∩ B
A - B
B - A
```

ve:

```text
Jaccard(A,B)
=
|A ∩ B| / |A ∪ B|
```

Bunun yanında sport ve `ch` distribution değişimine bakılacak.

---

# 2. WORKING PRINCIPLE

Ana çalışma prensibi:

```text
observe
→ experiment
→ prove
→ only then productionize
```

Her bulguyu mümkünse şu sınıflardan biriyle düşün:

```text
PROVEN
STRONG EVIDENCE
HYPOTHESIS
UNKNOWN
```

Protocol semantics tahmin edilmemeli.

Kullanıcı adım adım ilerlemek istiyor.

Bir deney tamamlanmadan başka mimari değişikliğe geçme.

Şu anda:

```text
NO production refactor
NO Java rewrite
NO DB
NO Redis
NO Kafka/RabbitMQ
NO premature architecture rewrite
```

Önce browser'ın gerçek davranışını çöz.

---

# 3. PROJECT

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

Prematch inspection:

```bash
uv run python inspect_prematch_games.py
```

Main:

```bash
uv run python main.py
```

---

# 4. PROJECT GOAL

Amaç browser bağımsız şekilde MyStake lifecycle verisini toplamak:

```text
fixture discovery
→ PREMATCH
→ prematch markets/selections/odds
→ prematch updates
→ kickoff
→ LIVE
→ live markets/odds/score
→ MATCH_ENDED
→ final state
→ cleanup
```

Sadece live maçlar değil.

Full lifecycle gerekiyor.

---

# 5. CURRENT STRUCTURE

Yaklaşık:

```text
mystake-collector/
├── docs/
│   ├── handoff/
│   └── product/
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

Rule:

```text
sources/
→ transport / protocol

pipeline/
→ decoding / transformation / diff

events/
→ semantic events
```

---

# 6. MQTT — PROVEN

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

Reconnect/resubscribe proven.

UNSUBSCRIBE/UNSUBACK proven.

Wildcard:

```text
live/gamenew/#
```

rejected:

```text
SUBACK 0x80
```

Exact live topic:

```text
live/gamenew/{GameId}
```

works.

---

# 7. CACHE INDIRECTION — PROVEN

MQTT PUBLISH çoğu zaman actual data taşımıyor.

Pattern:

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

# 8. LIVE SIDE — PROVEN

Live snapshot/diff works.

Football match-end strong pattern:

```text
Status == 3
BetStatus == 0
EventStatus == 40
```

Real lifecycle observed:

```text
subscribe
→ live updates
→ match-end
→ final state
→ cleanup
```

---

# 9. PREMATCH MQTT TOPICS — PROVEN

Global topics:

```text
prematch/header
prematch/games
prematch/markets
```

All verified via real MQTT traffic.

GameId-specific prematch MQTT topic not found.

---

# 10. PREMATCH/GAMES

Cache:

```text
https://wss-eu-uk1.ws-amazon.com/api/cache/get?key=prematch/games
```

Payload:

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

Important:

```text
prematch/games
IS NOT
full fixture universe
```

Current strongest interpretation:

```text
global change/invalidation/revalidation stream
```

Exact duplicates occur.

`DeleteList` semantics are not proven.

Do NOT automatically delete local state when DeleteList appears without proof.

---

# 11. PREMATCH/MARKETS

Topic:

```text
prematch/markets
```

Observed cache values decode to timestamps such as:

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

Not actual market data.

Exact semantics unresolved.

---

# 12. PREMATCH ENDPOINTS

Full:

```text
/api/prematch/getprematchgamefull/28/{GameId}
```

Actual host:

```text
https://analytics-sp.googleserv.tech
```

Current context ID:

```text
28
```

`28` is NOT sport ID.

Exact meaning unresolved.

Gameall:

```text
/api/prematch/getprematchgameall/{LANGUAGE}/28/?games=,{GAME_IDS}
```

Supports batching.

---

# 13. GAMEFULL — AUTHORITATIVE

Outer structure:

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

Real tests strongly establish:

```text
gamefull
=
full authoritative state snapshot
```

---

# 14. GAMEALL — PARTIAL

Real fixture comparison:

```text
gameall:
~9 KB
6 markets
81 selections
```

vs:

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
gameall = partial payload

gamefull = authoritative full snapshot
```

---

# 15. GAMEALL IS NOT A COMPLETE LOGICAL DELTA

Previous assumption:

```text
market absent
→ unchanged

market present
→ replace it
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

Selection identity can also change while selection count stays the same.

Therefore:

```text
gameall
=
partial representation

NOT authoritative snapshot
NOT complete logical delta
```

Do NOT spend more time trying to perfect gameall merge semantics yet.

---

# 16. PC CHECK

Observed strong relation:

```text
gamefull.pc
≈ total selection count
```

Implemented:

```text
local selection count != pc
→ gamefull resync
```

It detected real divergence.

But:

```text
pc match
≠ exact state correctness
```

Same-count authoritative divergence was observed.

For example local/full had same count while odds differed.

---

# 17. RECONCILIATION EXPERIMENT

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

~90.9% repair rate.

This demonstrates partial gameall merge does not maintain exact state.

Do NOT solve by:

```text
10 → 5 → 1
```

because that degenerates into blind full polling.

This is why browser reverse engineering began.

---

# 18. FIRST DETAIL-PAGE HAR

Important GameId:

```text
76433701
```

URL:

```text
https://mystake.com/en/sportsbook/prematch/match/76433701
```

Observed:

```text
prematch/games contains 76433701
→ shortly afterwards
gamefull/28/76433701
```

Example:

```text
20:55:05.979
prematch/games

20:55:06.540
gamefull

≈561 ms
```

Another:

```text
20:55:00.245
prematch/games

20:55:00.528
gamefull

≈283 ms
```

For that GameId:

```text
7 gamefull calls
7/7 correlated with preceding prematch/games signal
```

---

# 19. IMPORTANT INVALIDATION DISCOVERY

Two consecutive `gamefull` snapshots were identical:

```text
markets = 60 → 60
pc      = 267 → 267
mc      = 76 → 76
up      = same
vis     = same

markets changed    = 0
selections changed = 0
coef changes       = 0
lock changes       = 0
```

Therefore:

```text
prematch/games UpdateList
≠ guaranteed state/odds change
```

Better interpretation:

```text
prematch/games entry
≈
"GameId X should be revalidated/refreshed"
```

Later refresh actually contained:

```text
55 markets changed
194 selections changed
194 coef changes
```

So invalidation can return either:

```text
same authoritative state
```

or:

```text
new authoritative state
```

---

# 20. FIRST GLOBAL HAR

Results roughly:

```text
prematch/games entries ≈ 5139
distinct update GameIds ≈ 2006

gamefull calls = 172
distinct gamefull GameIds = 28
```

Therefore:

```text
prematch/games
=
global stream
```

But browser does NOT:

```text
every update GameId
→ gamefull
```

Instead only a relevant/active subset gets fetched.

Same-GameId gamefull correlation:

```text
≤0.5 sec → 88
≤1.0 sec → 153
≤1.5 sec → 162
≤2.0 sec → 164
≤3.0 sec → 167
```

Thus:

```text
164 / 172
≈95.3%
```

were correlated within 2 sec.

Also:

```text
28 / 28
```

distinct gamefull GameIds had at least one preceding same-GameId prematch/games signal.

---

# 21. CURRENT FRONTEND MODEL

Strongest evidence-based model:

```text
prematch/games
        ↓
global UpdateList
        ↓
frontend checks:
"is GameId relevant/active?"
        ↓
YES
        ↓
getprematchgamefull/28/{GameId}
        ↓
authoritative state refresh
```

Key unresolved question:

```text
How does frontend define relevant/active GameIds?
```

THIS is what we are currently investigating.

---

# 22. OLD CH HYPOTHESIS

Earlier HAR showed some GameIds grouped by same:

```text
ch
```

Example:

```text
ch=38220:
76365207
76369418
76433701
76433705
76462118
```

This suggested:

```text
competition/group
→ active GameId set
```

But NOT proven.

A clean single-league experiment was planned.

Because it was difficult to find a suitable competition with enough weekday fixtures, experiment changed to `/prematch/upcoming`.

---

# 23. UPCOMING PAGE

URL:

```text
https://mystake.com/en/sportsbook/prematch/upcoming
```

Page contains tabs such as:

```text
Soccer
Basketball
Tennis
...
```

Selecting another sport does NOT change the URL.

This is fine.

Likely active tab is frontend state.

Current experiment intentionally uses:

```text
same URL
same page
different selected tab
```

to isolate selected-sport effect.

---

# 24. FIVE SOCCER TEST FIXTURES

User previously identified:

```text
76433706
76433710
76433712
76433719
76473552
```

All were around 22:00.

They appeared under Soccer.

UI displayed something like:

```text
Res.
```

possibly Reserve/Reserves.

Whether reserve or not is irrelevant to protocol analysis.

From `soccer.har`:

```text
GameId       sport   ch     kickoff
76433706     1       8015   22:00
76433710     1       8015   22:00
76433712     1       8015   22:00
76433719     1       8015   22:00
76473552     1       8015   22:00
```

Thus these five clearly form same `ch=8015` group.

---

# 25. SOCCER.HAR — KNOWN BASELINE

This HAR MUST be reloaded/analyzed in new chat rather than trusting only this summary, because both HAR files will be available.

But known previous results are:

```text
HAR entries = 534

prematch/games cache calls = 227

gamefull calls = 103

distinct gamefull GameIds = 30

gameall calls = 0
```

Distinct GameIds by sport:

```text
sport=1 → 22
sport=2 → 8
```

gamefull request count by sport:

```text
sport=1 → 74
sport=2 → 29
```

Therefore:

```text
Soccer selected
≠ only Soccer data refreshed
```

This simple hypothesis was disproven.

---

# 26. SOCCER.HAR — TIMING CORRELATION

For all 103 gamefull requests:

```text
≤0.5 sec : 27
≤1.0 sec : 70
≤1.5 sec : 93
≤2.0 sec : 97
≤3.0 sec : 101
≤5.0 sec : 103
```

Thus:

```text
103 / 103
```

had same-GameId `prematch/games` signal within previous 5 seconds.

This provides extremely strong evidence for:

```text
prematch/games
→ relevant GameId decision
→ gamefull
```

---

# 27. SOCCER.HAR — THE FIVE TEST FIXTURES

Previously counted:

```text
76433706 → 3 gamefull calls
76433710 → 6
76433712 → 4
76433719 → 3
76473552 → 3
```

Their prematch/games → gamefull latencies were about:

```text
76433706 : 0.243–1.109 sec
76433710 : 0.243–1.469 sec
76433712 : 0.243–1.109 sec
76433719 : 0.243–1.110 sec
76473552 : 0.244–1.111 sec
```

---

# 28. SOCCER.HAR — CH OBSERVATION

Soccer-selected HAR did NOT only fetch `ch=8015`.

Multiple ch values appeared, including examples:

```text
107509
1091
114549
12431
30408
38220
5068
57632
583
58945
69525
8015
97089
...
```

Therefore this simple model does NOT fit `/upcoming`:

```text
current competition
→ only same ch refresh
```

The relevant set is broader.

---

# 29. GAMEALL — VERY IMPORTANT

First analyzed HAR:

```text
gamefull = 172
gameall = 0
```

Soccer upcoming HAR:

```text
gamefull = 103
gameall = 0
```

Thus across these two previous captures:

```text
275 observed gamefull requests
0 gameall requests
```

Correct interpretation:

```text
gameall was NOT part of the observed
realtime prematch refresh chain
```

Do NOT overclaim:

```text
"browser never uses gameall"
```

That is not proven.

Gameall may belong to another use-case such as bootstrap/list/etc.

But current realtime browser evidence strongly favors:

```text
prematch/games
→ gamefull
```

rather than:

```text
prematch/games
→ gameall
```

---

# 30. WHY BASKETBALL.HAR EXISTS

Soccer tab was selected but browser still refreshed:

```text
sport=1
AND
sport=2
```

Therefore next experiment was specifically designed to distinguish:

### Hypothesis A

```text
selected sport barely matters

/upcoming page maintains
a broad page-level relevant set
```

versus:

### Hypothesis B

```text
selected sport affects relevant set

but browser additionally keeps/preloads
some other sports
```

versus:

### Hypothesis C

```text
shared page-level core
+
selected-sport-specific fixtures
```

Basketball HAR is the experiment intended to separate these.

---

# 31. BASKETBALL.HAR TEST SETUP

Exactly:

```text
1. /prematch/upcoming already open
2. Basketball tab selected
3. AFTER switching:
   Network Clear
4. Preserve Log ON
5. No interaction
6. ~5 minutes observation
7. HAR exported
```

Important:

URL remains:

```text
https://mystake.com/en/sportsbook/prematch/upcoming
```

That does NOT invalidate experiment.

It is actually useful because route remains constant.

---

# 32. NEW CHAT: FIRST ANALYZE BOTH FILES FROM SCRATCH

Do not rely only on summary counts.

Read:

```text
soccer.har
basketball.har
```

programmatically.

Extract the same metrics from both with identical methodology.

For EACH HAR calculate:

```text
total HAR entries

observation start/end/duration

prematch/games request count

prematch/games decoded UpdateList count
if practical

distinct GameIds observed in prematch/games

gamefull request count

distinct gamefull GameIds

gameall request count

distinct gameall GameIds if any
```

Then decode relevant gamefull response data.

---

# 33. EXTRACT PER GAMEID

For every distinct `gamefull` GameId, preferably determine:

```text
GameId
sport
ch
up
pc
mc
kickoff/start timestamp
game name / participant names if conveniently available
number of gamefull calls
```

Do this for BOTH HARs.

This will allow clean set-level comparison.

---

# 34. MAIN SET COMPARISON

Define:

```text
A = soccer.har distinct gamefull GameIds

B = basketball.har distinct gamefull GameIds
```

Calculate EXACTLY:

```text
|A|
|B|

|A ∩ B|

|A - B|

|B - A|

|A ∪ B|
```

Then:

```text
Jaccard similarity
=
|A ∩ B| / |A ∪ B|
```

Show percentage.

Also list exact IDs in:

```text
shared

Soccer-only

Basketball-only
```

If lists are long, summarize counts first and then show IDs grouped cleanly.

---

# 35. COMPARE BY SPORT

For each HAR calculate BOTH:

```text
distinct GameIds by sport
```

and:

```text
gamefull calls by sport
```

Example Soccer baseline from previous analysis:

```text
Soccer HAR:

sport=1:
22 distinct GameIds
74 calls

sport=2:
8 distinct GameIds
29 calls
```

Recalculate this from `soccer.har` to verify.

Then do Basketball HAR.

Important question:

```text
Did Basketball selection materially increase sport=2?
```

And:

```text
Did sport=1 shrink?
```

But do not just compare proportions.

Compare exact IDs.

---

# 36. SPORT-SPECIFIC SET COMPARISON

Calculate:

```text
Soccer HAR sport=1 set
vs
Basketball HAR sport=1 set
```

and:

```text
Soccer HAR sport=2 set
vs
Basketball HAR sport=2 set
```

For each show:

```text
shared
removed
added
Jaccard
```

This is critical.

Example question:

```text
Are the 8 sport=2 GameIds seen while Soccer was selected
the exact same Basketball GameIds that continue when Basketball is selected?
```

If yes, interesting.

If Basketball adds many new sport=2 fixtures, also important.

---

# 37. COMPARE CH DISTRIBUTIONS

For both HARs calculate:

```text
ch → distinct GameId count
ch → gamefull request count
```

Compare.

Questions:

```text
Does Basketball selection introduce new ch groups?

Do Soccer ch groups disappear?

Is there a shared set of ch groups?

Does one dominant Basketball ch appear?
```

Do NOT assume `ch` is literally competition ID unless evidence becomes sufficient.

Use:

```text
group/competition-like identifier
```

if semantics remain uncertain.

---

# 38. PREMATCH/GAMES → GAMEFULL CORRELATION

For EACH gamefull call:

Find the latest prior decoded:

```text
prematch/games UpdateList
```

entry for the SAME GameId.

Calculate delta:

```text
gamefull timestamp
-
prematch/games occurrence timestamp
```

For basketball.har generate buckets:

```text
≤0.5 sec
≤1 sec
≤1.5 sec
≤2 sec
≤3 sec
≤5 sec
```

Compare with soccer.har.

Known previous soccer result:

```text
≤0.5 : 27
≤1.0 : 70
≤1.5 : 93
≤2.0 : 97
≤3.0 : 101
≤5.0 : 103
```

Recalculate from file.

If Basketball shows same pattern, the invalidation model receives another independent confirmation.

---

# 39. ALSO LOOK FOR FETCH BURSTS

Earlier capture contained cases where:

```text
one prematch/games signal
→ multiple gamefull requests
```

over several seconds.

So do not assume:

```text
1 notification = exactly 1 fetch
```

Detect if basketball.har contains burst behavior.

Possible analysis:

```text
same GameId
multiple gamefull calls
following one UpdateList occurrence
```

Mention if observed.

---

# 40. COMPARE REQUEST CADENCE

Useful secondary metrics:

```text
gamefull requests/minute

prematch/games requests/minute

distinct relevant GameIds/minute
```

Because HAR duration may not be exactly identical.

Do NOT compare raw request counts blindly if durations differ materially.

Normalize where useful.

---

# 41. IMPORTANT QUESTIONS THE TWO HARs MUST ANSWER

Answer these explicitly after analysis:

```text
1. Does selecting Basketball materially change
   the gamefull GameId set?

2. What percentage of relevant GameIds are shared?

3. Which GameIds disappear?

4. Which GameIds appear?

5. Does sport=2 representation increase?

6. Does sport=1 representation decrease?

7. Are background Soccer fixtures still refreshed
   while Basketball is selected?

8. Are the sport=2 fixtures from soccer.har
   the same ones in basketball.har?

9. Does ch distribution change?

10. Does prematch/games → gamefull correlation remain?

11. Does gameall remain absent?

12. Is selected tab likely:
    - data-subscription filter,
    - partial influence,
    - or mostly presentation/UI filter?
```

---

# 42. INTERPRETATION MATRIX

Do NOT choose outcome before analysis.

## Outcome A — Sets nearly identical

If:

```text
high Jaccard
same exact GameIds
similar sport distribution
```

then strong evidence:

```text
selected sport tab is NOT the main relevant-set selector
```

Potential model:

```text
/upcoming page owns broad active dataset
selected tab mostly controls presentation
```

Still phrase carefully.

---

## Outcome B — Sets strongly different

If:

```text
many sport=1 disappear
many new sport=2 appear
low Jaccard
```

then:

```text
selected sport materially influences relevant fixture set
```

Since Soccer HAR already contained sport=2, possible model:

```text
selected sport fixtures
+
background/preloaded fixtures
```

---

## Outcome C — Shared core + meaningful delta

For example:

```text
50% common
some Soccer-only
some Basketball-only
```

then likely:

```text
page-level shared relevant core
+
selected-tab-specific relevant subset
```

Quantify the core and delta.

This may be the most architecturally useful result.

---

# 43. UPDATE TIMESTAMP CONTEXT

Previously observed relation:

```text
prematch/games.UpdateTimeStamp
```

and:

```text
gamefull.game.up
```

Examples:

```text
1790013300367 → 1790013300
1790013300    → 1790013300
1790013330430 → 1790013330
1790013331    → 1790013330
```

Strong evidence:

```text
UpdateTimeStamp
≈ notification/update timing

game.up
≈ authoritative state version/update timing
```

But DO NOT state:

```text
UpdateTimeStamp == game.up
```

as exact semantics.

---

# 44. CURRENT LIKELY ARCHITECTURE SHIFT

Existing collector currently roughly does:

```text
prematch/games
→ gameall
→ partial merge
→ pc check
→ periodic gamefull reconciliation
```

Observed browser increasingly looks like:

```text
prematch/games
→ check whether GameId is relevant
→ gamefull
→ authoritative refresh
```

This is a major finding.

However:

```text
DO NOT MODIFY COLLECTOR YET
```

until this Soccer/Basketball relevant-set experiment is analyzed.

The unresolved design question is:

```text
What relevant GameId universe should a headless collector maintain?
```

Browser may only care about UI-visible/current data.

Our collector ultimately wants broader lifecycle coverage.

Therefore browser's exact relevance rule may teach protocol semantics without necessarily being copied 1:1 into collector.

Keep that distinction in mind.

---

# 45. VERY IMPORTANT PRODUCT GOAL DISTINCTION

Browser objective:

```text
refresh whatever current UI needs
```

Collector objective:

```text
collect full fixture lifecycle
possibly broader than any one visible page
```

Therefore after understanding browser logic we may decide collector needs its own relevant set.

But browser is still the reference for:

```text
what does prematch/games signal mean?

what endpoint should be used for authoritative refresh?

how does frontend respond to invalidation?
```

---

# 46. DO NOT CLAIM YET

Do NOT claim:

```text
every prematch/games update causes gamefull
```

False.

Do NOT claim:

```text
selected Soccer means only Soccer is active
```

Disproven.

Do NOT claim:

```text
selected Basketball will mean only Basketball
```

Needs current HAR analysis.

Do NOT claim:

```text
ch = exact competition ID
```

Not fully proven.

Do NOT claim:

```text
gameall is never used
```

Not proven.

Do NOT claim:

```text
gameall should immediately be deleted from collector
```

Too early.

Do NOT claim:

```text
browser relevance algorithm should equal collector relevance algorithm
```

They have different goals.

---

# 47. PROVEN / STRONG FACTS BEFORE NEW HAR ANALYSIS

```text
MQTT connection                          ✅
reconnect/resubscribe                    ✅
unsubscribe                              ✅
cache indirection                        ✅

live exact GameId topic                  ✅
live diff                                ✅
match end                                ✅

prematch/header                          ✅
prematch/games                           ✅
prematch/markets                         ✅

gameall endpoint                         ✅
gamefull endpoint                        ✅

gameall partial                          ✅

gameall complete logical delta           ❌ disproven
missing gameall market = unchanged       ❌ disproven
pc equality = exact-state equality       ❌ disproven

gamefull authoritative refresh           ✅

prematch/games → same GameId gamefull    ✅ very strong

detail fixture correlation:
76433701                                 7/7

first global HAR:
same GameId ≤2 sec                       164/172

first global HAR:
all 28 distinct fetched GameIds had
preceding update signal                  ✅

soccer.har previous analysis:
gamefull calls                           103
distinct gamefull IDs                    30
gameall                                  0

Soccer selected:
sport=1 distinct                         22
sport=2 distinct                         8

therefore selected Soccer
!= exclusive sport=1 data                ✅

exact relevant-set selection logic       UNKNOWN

effect of switching to Basketball        CURRENT EXPERIMENT
```

---

# 48. EXPECTED OUTPUT AFTER TWO-HAR ANALYSIS

Do not return only a vague explanation.

Give a compact quantitative comparison similar to:

```text
                         Soccer       Basketball
------------------------------------------------
Duration                 ...
gamefull calls           ...
distinct GameIds         ...
gameall calls            ...
sport=1 distinct         ...
sport=2 distinct         ...
sport=1 calls            ...
sport=2 calls            ...
distinct ch              ...
```

Then set comparison:

```text
Soccer GameIds      = X
Basketball GameIds  = Y
Shared              = Z

Soccer-only         = ...
Basketball-only     = ...

Jaccard             = ...%
```

Then sport-specific comparison.

Then timing correlation.

Then conclusion classified as:

```text
PROVEN FROM THESE HARS

STRONG EVIDENCE

STILL UNKNOWN
```

Finally recommend **one next experiment only**, if another experiment is actually necessary.

---

# 49. THE MOST IMPORTANT CURRENT QUESTION

Do not get distracted by implementation yet.

Right now answer this:

```text
When /prematch/upcoming stays on the same route,
does changing selected sport from Soccer to Basketball
materially change the set of GameIds that MyStake
refreshes with getprematchgamefull?
```

The two uploaded HARs were specifically collected to answer this.

Analyze them first.

---

# 50. NEW CHAT OPENING

After pasting this context, user will upload:

```text
soccer.har
basketball.har
```

Treat filenames as authoritative experiment labels:

```text
soccer.har
= Soccer selected

basketball.har
= Basketball selected
```

Immediately analyze both.

Do not ask what to do with them.

Do not request the files again if both are attached.

Do not change collector code yet.

Goal:

```text
exactly quantify how sport-tab selection
changes—or does not change—
the frontend relevant GameId set.
```
