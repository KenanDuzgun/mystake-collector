# MyStake Collector — Continuation Context / Handoff

## 0. NEW CHAT — FIRST TASK

Bu doküman yeni ChatGPT chat session'ına verilecek.

Kullanıcı ardından iki YENİ HAR dosyası yükleyecek:

```text
soccer.har
basketball.har
```

Bunlar önceki HAR'lardan farklı olarak **aynı anda / paralel olarak** kaydedilecek.

Amaç:

```text
time progression / fixture lifecycle churn
```

ile:

```text
selected sport tab effect
```

etkilerini birbirinden ayırmak.

Yeni chat'te:

1. Bu handoff'u oku.
2. Kullanıcının yüklediği YENİ `soccer.har` ve `basketball.har` dosyalarını programmatically analiz et.
3. Dosyaları yeniden isteme.
4. Ne yapılacağını sorma.
5. Collector kodunu henüz değiştirme.
6. Önce paralel HAR deneyinin sonucunu kesin şekilde çıkar.

---

# 1. CURRENT CORE RESEARCH QUESTION

Şu anda çözmeye çalıştığımız ana problem:

```text
How does MyStake frontend determine
which prematch GameIds are relevant/active?
```

Özellikle:

```text
/prematch/upcoming
```

sayfasında seçili sport tab:

```text
Soccer
Basketball
Tennis
...
```

frontend'in hangi GameId'leri:

```text
getprematchgamefull
```

ile refresh edeceğini etkiliyor mu?

Ana soru:

```text
Does selected sport materially influence
the frontend relevant GameId universe?
```

---

# 2. WORKING PRINCIPLE

Her zaman:

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

Protocol semantics tahmin edilmemeli.

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

Amaç browser bağımsız şekilde MyStake betting data lifecycle'ını toplamak.

Full lifecycle:

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

Sadece live maçları toplamak istemiyoruz.

Collector'ın amacı:

```text
full fixture lifecycle
```

---

# 5. CURRENT PROJECT STRUCTURE

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

Proven:

```text
reconnect/resubscribe ✅
UNSUBSCRIBE/UNSUBACK ✅
```

Wildcard:

```text
live/gamenew/#
```

rejected with:

```text
SUBACK 0x80
```

Exact live topic works:

```text
live/gamenew/{GameId}
```

---

# 7. CACHE INDIRECTION — PROVEN

MQTT PUBLISH çoğu zaman actual payload taşımıyor.

Observed pattern:

```text
MQTT PUBLISH
→ cache URL
→ HTTP GET
→ base64 decode
→ optional gzip
→ JSON decode
```

Bu implemented ve proven.

---

# 8. LIVE SIDE — PROVEN

Live snapshot/diff çalışıyor.

Football match end için güçlü pattern:

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

# 9. PREMATCH MQTT TOPICS — PROVEN

Global topics:

```text
prematch/header
prematch/games
prematch/markets
```

Hepsi gerçek MQTT trafiğinde doğrulandı.

GameId-specific prematch MQTT topic bulunmadı.

---

# 10. PREMATCH/GAMES

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

Important:

```text
prematch/games
IS NOT
the complete fixture universe
```

Strongest interpretation:

```text
prematch/games
=
global change / invalidation / revalidation stream
```

Exact duplicate notifications occur.

`DeleteList` exact semantics are still NOT proven.

Do not automatically delete local state solely because a GameId appears in DeleteList until semantics are proven.

---

# 11. PREMATCH/MARKETS

Topic:

```text
prematch/markets
```

Observed cache payloads decoded into timestamp/version-like values such as:

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

It does NOT appear to carry actual market state.

Exact semantics unresolved.

---

# 12. PREMATCH HTTP ENDPOINTS

Full snapshot:

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

Important:

```text
28 is NOT sport ID
```

Exact meaning unresolved.

Partial endpoint:

```text
/api/prematch/getprematchgameall/{LANGUAGE}/28/?games=,{GAME_IDS}
```

Supports batching.

---

# 13. GAMEFULL — AUTHORITATIVE

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

Real comparisons strongly establish:

```text
gamefull
=
full authoritative state snapshot
```

---

# 14. GAMEALL — PARTIAL

Real fixture comparison previously showed approximately:

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
gamefull = authoritative full snapshot
```

---

# 15. GAMEALL IS NOT A COMPLETE LOGICAL DELTA

Earlier assumption:

```text
market absent
→ unchanged

market present
→ replace market
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

Selection identity can change while selection count remains the same.

Therefore:

```text
gameall
=
partial representation

NOT authoritative snapshot
NOT complete logical delta
```

Do not spend more time trying to reverse engineer perfect `gameall` merge semantics yet.

---

# 16. PC CHECK

Observed strong relation:

```text
gamefull.pc
≈ total selection count
```

Collector implemented:

```text
local selection count != pc
→ gamefull resync
```

This detected real divergence.

However:

```text
pc match
≠ exact state correctness
```

Same-count authoritative divergence was observed.

For example:

```text
same pc
same number of selections
different odds/state
```

Therefore `pc` can detect some corruption but cannot prove exact equality.

---

# 17. RECONCILIATION EXPERIMENT

Experimental setting:

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

10-minute soak result:

```text
checks  = 11
matches = 1
repairs = 10
```

Approximately:

```text
90.9% repair rate
```

Meaning partial gameall merging does NOT maintain authoritative state.

Do NOT solve by simply changing:

```text
10 → 5 → 1
```

because that degenerates into blind full polling.

This result triggered frontend/browser reverse engineering.

---

# 18. IMPORTANT DETAIL-PAGE HAR

Important GameId:

```text
76433701
```

URL:

```text
https://mystake.com/en/sportsbook/prematch/match/76433701
```

Observed repeatedly:

```text
prematch/games contains 76433701
→ shortly afterwards
getprematchgamefull/28/76433701
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

For GameId 76433701:

```text
7 gamefull calls
7/7 preceded by same-GameId prematch/games signal
```

---

# 19. INVALIDATION DISCOVERY

Two consecutive `gamefull` responses after notifications were completely identical:

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
≠ guaranteed state change
```

Better interpretation:

```text
prematch/games UpdateList entry
≈
"GameId X should be revalidated/refreshed"
```

Another later refresh after notification DID change state:

```text
55 markets changed
194 selections changed
194 coef changes
```

So notification can lead to:

```text
same authoritative state
```

or:

```text
new authoritative state
```

This is classic invalidation/revalidation-like behavior.

---

# 20. FIRST GLOBAL HAR

One earlier global HAR roughly showed:

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

Browser does NOT perform:

```text
every UpdateList GameId
→ gamefull
```

Instead only a relevant/active subset gets refreshed.

Same-GameId correlation:

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

were correlated within two seconds.

Also:

```text
28 / 28
```

distinct fetched GameIds had at least one preceding same-GameId notification.

---

# 21. CURRENT FRONTEND MODEL

Strongest evidence-based model:

```text
prematch/games
        ↓
global UpdateList
        ↓
frontend asks:
"is this GameId relevant/active?"
        ↓
YES
        ↓
getprematchgamefull/28/{GameId}
        ↓
authoritative state refresh
```

Key unresolved problem:

```text
How does frontend define relevant/active GameIds?
```

---

# 22. OLD CH HYPOTHESIS

Earlier some GameIds shared same:

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

This suggested a competition/group relation.

But exact semantics remain unresolved.

Do NOT state:

```text
ch = competition ID
```

as proven.

Safer terminology:

```text
group/competition-like identifier
```

A single-league experiment was considered, but weekday fixture availability made it inconvenient.

Investigation moved to:

```text
/prematch/upcoming
```

---

# 23. UPCOMING PAGE

URL:

```text
https://mystake.com/en/sportsbook/prematch/upcoming
```

Contains tabs such as:

```text
Soccer
Basketball
Tennis
...
```

Selecting another sport does NOT change the URL.

Likely selected sport is frontend/client state.

This is expected and useful because it isolates:

```text
same route
different selected tab
```

---

# 24. FIVE IMPORTANT SOCCER FIXTURES

Previously selected test fixtures:

```text
76433706
76433710
76433712
76433719
76473552
```

They appeared under Soccer around 22:00 local time.

UI displayed something resembling:

```text
Res.
```

possibly reserves.

Reserve status is irrelevant to current protocol analysis.

Decoded data:

```text
GameId       sport   ch
76433706     1       8015
76433710     1       8015
76433712     1       8015
76433719     1       8015
76473552     1       8015
```

Therefore these five clearly belong to the same:

```text
ch=8015
```

group.

---

# 25. PREVIOUS SOCCER HAR

Previous `soccer.har`:

```text
Soccer selected
Network cleared AFTER selecting Soccer
Preserve Log ON
~5 minutes observation
no interaction
```

Results:

```text
HAR entries = 534

prematch/games requests = 227
decoded UpdateList entries = 890
distinct prematch/games GameIds = 226

gamefull calls = 103
distinct gamefull GameIds = 30

gameall calls = 0
```

Distinct GameIds by sport:

```text
sport=1 → 22
sport=2 → 8
```

gamefull calls by sport:

```text
sport=1 → 74
sport=2 → 29
```

Therefore:

```text
Soccer selected
≠ only Soccer fixtures refreshed
```

This is PROVEN.

---

# 26. PREVIOUS SOCCER HAR TIMING CORRELATION

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

had same-GameId `prematch/games` signal during previous 5 seconds.

Very strong evidence:

```text
prematch/games
→ relevance check
→ gamefull
```

---

# 27. FIVE TEST FIXTURES IN PREVIOUS SOCCER HAR

Previous counts:

```text
76433706 → 3 gamefull calls
76433710 → 6
76433712 → 4
76433719 → 3
76473552 → 3
```

Approx notification→gamefull latency:

```text
76433706 : 0.243–1.109 sec
76433710 : 0.243–1.469 sec
76433712 : 0.243–1.109 sec
76433719 : 0.243–1.110 sec
76473552 : 0.244–1.111 sec
```

---

# 28. PREVIOUS SOCCER HAR CH RESULT

Soccer-selected capture did NOT only fetch `ch=8015`.

Multiple `ch` groups appeared, including:

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

Therefore simple model:

```text
one current competition
→ only one ch group refreshed
```

does NOT fit `/prematch/upcoming`.

The relevant universe is broader.

---

# 29. GAMEALL REALTIME OBSERVATION

First global HAR:

```text
gamefull = 172
gameall = 0
```

Previous Soccer HAR:

```text
gamefull = 103
gameall = 0
```

Previous Basketball HAR:

```text
gamefull = 138
gameall = 0
```

Across these captures:

```text
413 observed gamefull requests
0 observed gameall requests
```

Correct interpretation:

```text
gameall was NOT observed as part of
the realtime prematch invalidation refresh chain
```

Do NOT claim:

```text
browser never uses gameall
```

It may still be used for bootstrap/list/another flow.

But realtime evidence strongly favors:

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

# 30. PREVIOUS SOCCER VS BASKETBALL EXPERIMENT

An experiment was performed with:

```text
soccer.har
basketball.har
```

However they were NOT recorded simultaneously.

Soccer capture:

```text
Soccer selected
~5 minute observation
```

Basketball capture:

```text
Basketball selected
~5 minute observation
```

The Basketball capture started approximately 13 minutes after the Soccer capture.

This turned out to be an important confounding variable.

---

# 31. PREVIOUS TWO-HAR SUMMARY

Previous HAR comparison:

```text
                         Soccer        Basketball
--------------------------------------------------
HAR entries              534           486
Duration                  5m25s         4m55s
prematch/games            227           171
UpdateList entries        890           605
distinct notified IDs     226           186
gamefull calls            103           138
distinct gamefull IDs     30            36
gameall calls             0             0
sport=1 distinct          22            30
sport=2 distinct          8             6
sport=1 calls             74            121
sport=2 calls             29            17
distinct ch               18            18
```

Important surprise:

```text
Soccer selected:
sport=1 → 22 distinct
sport=2 → 8 distinct

Basketball selected:
sport=1 → 30 distinct
sport=2 → 6 distinct
```

Therefore Basketball selection did NOT lead to obvious increased Basketball subscription.

In fact Soccer became MORE dominant in the later capture.

---

# 32. PREVIOUS GAMEID SET COMPARISON

Define:

```text
A = previous soccer.har distinct gamefull GameIds
B = previous basketball.har distinct gamefull GameIds
```

Results:

```text
|A| = 30
|B| = 36

|A ∩ B| = 24

A - B = 6
B - A = 12

|A ∪ B| = 42
```

Jaccard:

```text
24 / 42
= 57.14%
```

So there was a large common relevant core.

---

# 33. PREVIOUS SHARED 24 GAMEIDS

```text
73869181
74463331
76011135
76084213
76243887
76262444
76295713
76298554
76298562
76365661
76396622
76433706
76433710
76433712
76433719
76442441
76447231
76473274
76473276
76473350
76473351
76473352
76473355
76473552
```

Important:

All five `ch=8015` Soccer test fixtures remained active in the Basketball capture:

```text
76433706
76433710
76433712
76433719
76473552
```

This proves:

```text
Basketball selected
≠ stop refreshing Soccer fixtures
```

---

# 34. PREVIOUS SOCCER-ONLY GAMEIDS

```text
76207230   sport=2
76243885   sport=2
76397517   sport=2
76397549   sport=2
76432070   sport=1
76433699   sport=1
```

Interesting:

```text
4 / 6 disappearing fixtures were Basketball
```

Thus switching to Basketball certainly did NOT simply retain/add every Basketball fixture.

---

# 35. PREVIOUS BASKETBALL-ONLY GAMEIDS

```text
73869178   sport=2
75946238   sport=1
76340642   sport=1
76396675   sport=1
76396700   sport=1
76432071   sport=1
76447096   sport=1
76447116   sport=1
76447163   sport=1
76447198   sport=1
76447298   sport=1
76483288   sport=2
```

Composition:

```text
10 Soccer
2 Basketball
```

Again, this does NOT look like:

```text
select Basketball
→ subscribe primarily to Basketball
```

---

# 36. PREVIOUS SPORT=1 SET COMPARISON

Soccer fixtures:

```text
Soccer HAR     = 22
Basketball HAR = 30

shared  = 20
removed = 2
added   = 10

Jaccard = 20 / 32
        = 62.5%
```

Removed:

```text
76432070
76433699
```

Added:

```text
75946238
76340642
76396675
76396700
76432071
76447096
76447116
76447163
76447198
76447298
```

Thus Basketball tab did NOT reduce Soccer representation.

Observed Soccer distinct fixtures actually increased:

```text
22 → 30
```

---

# 37. PREVIOUS SPORT=2 SET COMPARISON

Basketball fixtures:

```text
Soccer HAR     = 8
Basketball HAR = 6

shared  = 4
removed = 4
added   = 2

Jaccard = 4 / 10
        = 40%
```

Shared:

```text
73869181
76243887
76298554
76298562
```

Removed:

```text
76207230
76243885
76397517
76397549
```

Added:

```text
73869178
76483288
```

So the 8 Basketball GameIds seen with Soccer selected did NOT all remain when Basketball was selected.

Only:

```text
4 / 8
```

continued.

---

# 38. PREVIOUS CH DISTRIBUTION

Both HARs:

```text
18 distinct ch
```

Shared `ch` values:

```text
583
657
1091
5068
5779
8015
38220
46061
57632
58945
69525
97089
107509
114549
```

Therefore:

```text
14 / 18 ch groups shared
```

Soccer-only:

```text
12431
30408
108287
113921
```

Basketball-only:

```text
5092
20189
57637
104276
```

Again this looked like:

```text
large shared page-level core
+
some churn
```

rather than two entirely independent sport datasets.

---

# 39. PREVIOUS BASKETBALL TIMING CORRELATION

Basketball HAR:

```text
138 gamefull calls
```

Correlation buckets:

```text
≤0.5 sec : 30
≤1.0 sec : 75
≤1.5 sec : 82
≤2.0 sec : 82
≤3.0 sec : 82
≤5.0 sec : 133
```

The remaining five also had an earlier same-GameId notification.

Maximum observed delay roughly:

```text
7.6 sec
```

Therefore:

```text
138 / 138
```

had a preceding same-GameId `prematch/games` notification in the capture.

This independently reinforced the invalidation model.

---

# 40. FETCH BURSTS — IMPORTANT

Do NOT assume:

```text
1 UpdateList occurrence
=
exactly 1 gamefull request
```

Observed:

```text
one prematch/games occurrence
→ several gamefull calls for same GameId
```

Previous Soccer HAR had burst groups reaching approximately:

```text
6 gamefull requests
```

Previous Basketball HAR approximately:

```text
7 gamefull requests
```

So semantics are closer to:

```text
notification/revalidation trigger
→ frontend scheduling/refetch logic
→ one or more authoritative fetches
```

Do NOT model notification/fetch as strict 1:1.

---

# 41. CRITICAL CONFOUND IN PREVIOUS EXPERIMENT

Previous captures were sequential.

Approximately:

```text
Soccer HAR:
18:39:44 UTC
→ 18:45:09 UTC

Basketball HAR:
18:58:46 UTC
→ 19:03:41 UTC
```

There was roughly:

```text
13 minutes
```

between them.

During those 13 minutes fixture lifecycle naturally changed.

Examples of Soccer-only fixtures:

```text
76397517  start ~18:40
76397549  start ~18:40

76432070  start ~18:45
76433699  start ~18:45
```

These had already reached/passed kickoff by the later Basketball capture.

New Basketball-HAR Soccer fixtures included several with around:

```text
19:00
```

kickoff:

```text
76396675
76396700
76447096
76447163
76447198
76447298
```

Therefore:

```text
A - B
and
B - A
```

could NOT safely be attributed to selected sport.

A large part may simply have been:

```text
time progression
+
fixture lifecycle churn
```

This is exactly why the NEXT experiment uses parallel capture.

---

# 42. CURRENT CONCLUSIONS BEFORE PARALLEL EXPERIMENT

## PROVEN

```text
prematch/games is a broad/global stream.
```

```text
Browser does NOT fetch every notified GameId.
```

```text
For relevant GameIds, same-GameId prematch/games
strongly precedes gamefull.
```

```text
gamefull is the authoritative snapshot.
```

```text
gameall is partial.
```

```text
gameall is NOT a complete logical delta.
```

```text
pc equality does NOT guarantee exact state equality.
```

```text
Soccer selected does NOT mean only Soccer fixtures
are refreshed.
```

```text
Basketball selected does NOT prevent Soccer fixtures
from being refreshed.
```

```text
The five ch=8015 Soccer fixtures continued refreshing
with Basketball selected.
```

```text
gameall has not been observed in the current
realtime prematch invalidation→refresh chain.
```

---

# 43. STRONG EVIDENCE BEFORE PARALLEL TEST

Current best-fitting model:

```text
/prematch/upcoming
        ↓
broad page-level relevant GameId universe
        ↓
selected sport may be mostly presentation state
or may have only partial influence
        ↓
prematch/games global invalidation stream
        ↓
frontend checks whether GameId is relevant
        ↓
getprematchgamefull
        ↓
authoritative state refresh
```

Strong evidence currently says:

```text
selected sport is NOT the primary/exclusive
data-subscription selector
```

However:

```text
selected sport has ZERO influence
```

is NOT proven yet.

That is precisely what the parallel HAR experiment must test.

---

# 44. STILL UNKNOWN

Do NOT claim these yet:

```text
selected sport has no network/data effect at all
```

```text
ch is exactly competition ID
```

```text
gameall is never used
```

```text
every notification causes a gamefull
```

```text
browser relevance algorithm should be copied
1:1 into collector
```

```text
DeleteList means immediately delete fixture
```

```text
28 is sport ID
```

All remain unresolved or disproven assumptions.

---

# 45. IMPORTANT BROWSER VS COLLECTOR DISTINCTION

Browser's objective:

```text
refresh whatever the current UI needs
```

Collector's objective:

```text
collect complete fixture lifecycle
possibly broader than a single page's visible data
```

Therefore even once browser relevance semantics are understood:

```text
browser relevant set
≠ necessarily collector relevant set
```

But browser remains our protocol reference for:

```text
what prematch/games means

what endpoint to call after invalidation

how revalidation works

how frontend chooses whether a notification matters
```

---

# 46. NEXT EXPERIMENT — PARALLEL HAR CAPTURE

THIS IS THE NEXT IMMEDIATE TASK.

Two browser windows/tabs will be recorded **at the same time**.

One:

```text
/prematch/upcoming
Soccer selected
```

Other:

```text
/prematch/upcoming
Basketball selected
```

Files:

```text
soccer.har
basketball.har
```

These new filenames REPLACE the previous files for the next analysis.

Treat filenames as experiment labels:

```text
soccer.har
=
parallel capture with Soccer selected

basketball.har
=
parallel capture with Basketball selected
```

---

# 47. PARALLEL CAPTURE PROCEDURE

Desired setup:

## Window A

```text
Open /prematch/upcoming
Select Soccer
Open DevTools → Network
Preserve Log ON
Clear Network
```

## Window B

```text
Open /prematch/upcoming
Select Basketball
Open DevTools → Network
Preserve Log ON
Clear Network
```

Then start both captures as close together as practically possible.

Do not interact with either page.

Observe approximately:

```text
5 minutes
```

Export:

```text
Window A → soccer.har
Window B → basketball.har
```

Most important point:

```text
captures must overlap almost completely in wall-clock time
```

This removes the previous experiment's biggest confound.

---

# 48. NEW CHAT — FIRST ANALYSIS OF PARALLEL FILES

When user uploads the NEW two HARs:

```text
soccer.har
basketball.har
```

analyze them programmatically from scratch.

Do NOT rely only on previous counts.

For EACH HAR calculate:

```text
total HAR entries

capture start timestamp
capture end timestamp
duration

overlap window between HARs

prematch/games request count

decoded UpdateList entry count

distinct notified GameIds

gamefull request count

distinct gamefull GameIds

gameall request count

distinct gameall GameIds if present
```

The overlap period is especially important.

---

# 49. VERY IMPORTANT — COMPARE ONLY COMMON TIME WINDOW WHEN POSSIBLE

Because one browser capture may start a few seconds earlier or stop a few seconds later:

Determine:

```text
overlap_start = max(soccer_start, basketball_start)

overlap_end = min(soccer_end, basketball_end)
```

Primary comparison should ideally use:

```text
only requests/events inside overlap_start → overlap_end
```

Also optionally show whole-file metrics separately.

This prevents start/stop timing artifacts.

---

# 50. EXTRACT GAMEFULL METADATA

For each distinct gamefull GameId determine where possible:

```text
GameId
sport
ch
up
pc
mc
kickoff/start timestamp
fixture/team names
number of gamefull calls
first fetch timestamp
last fetch timestamp
```

Do this independently for both HARs.

---

# 51. MAIN PARALLEL SET COMPARISON

Define using the COMMON OVERLAP WINDOW:

```text
A = Soccer selected distinct gamefull GameIds
B = Basketball selected distinct gamefull GameIds
```

Calculate exactly:

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
Jaccard(A,B)
=
|A ∩ B| / |A ∪ B|
```

Show exact percentage.

List:

```text
Shared GameIds
Soccer-only GameIds
Basketball-only GameIds
```

Include metadata for difference IDs:

```text
sport
ch
kickoff
fixture name if available
```

---

# 52. THIS TIME DIFFERENCE SETS ARE MUCH MORE IMPORTANT

Because captures are simultaneous:

```text
A - B
```

is much stronger evidence for:

```text
GameIds relevant with Soccer selected
but not Basketball selected
```

and:

```text
B - A
```

is much stronger evidence for:

```text
GameIds relevant with Basketball selected
but not Soccer selected
```

Unlike previous sequential capture, fixture time progression should no longer explain most differences.

Still check for request randomness/race effects before declaring deterministic behavior.

---

# 53. COMPARE PREMATCH/GAMES INPUT STREAM ITSELF

This is CRITICAL in the parallel experiment.

Compare decoded notification universes:

```text
N_soccer
=
distinct GameIds appearing in soccer.har prematch/games

N_basketball
=
distinct GameIds appearing in basketball.har prematch/games
```

Calculate:

```text
intersection
Soccer-only
Basketball-only
Jaccard
```

Also compare notification occurrence timestamps for shared GameIds.

Question:

```text
Are both pages receiving essentially the same
prematch/games global invalidation stream?
```

If yes, this gives us an excellent controlled experiment:

```text
same global notification input
+
different selected tab
→ compare gamefull output
```

That is much stronger than comparing only gamefull sets.

---

# 54. NOTIFICATION → FETCH DECISION MATRIX

For each GameId notified during the overlapping observation window, classify:

```text
notified in Soccer HAR?
notified in Basketball HAR?

gamefull fetched in Soccer HAR?
gamefull fetched in Basketball HAR?

sport
ch
```

Useful conceptual table:

```text
GameId    Notify S   Notify B   Fetch S   Fetch B   sport   ch
----------------------------------------------------------------
X         yes        yes        yes       yes
Y         yes        yes        yes       no
Z         yes        yes        no        yes
...
```

The MOST valuable GameIds are:

```text
same notification seen by BOTH pages
but gamefull fetched by only ONE page
```

Those would be direct evidence that some page/client relevance state changes the decision after receiving the same global invalidation.

Then inspect whether the asymmetric fetch aligns with:

```text
selected sport
```

or something else.

---

# 55. SELECTED-SPORT CAUSAL TEST

Specifically inspect shared notifications where:

```text
same GameId notified in both browser sessions
```

For `sport=1`:

Question:

```text
Does Soccer-selected page fetch the GameId
while Basketball-selected page ignores it?
```

For `sport=2`:

Question:

```text
Does Basketball-selected page fetch the GameId
while Soccer-selected page ignores it?
```

If a strong directional pattern appears, selected tab materially influences relevance.

If fetch decisions are nearly identical regardless of sport, selected tab is mostly presentation/UI state.

This is the core purpose of the experiment.

---

# 56. SPORT DISTRIBUTION

For each HAR calculate:

```text
distinct gamefull GameIds by sport
```

and:

```text
gamefull request count by sport
```

Then specifically compare:

```text
Soccer selected:
sport=1
sport=2

Basketball selected:
sport=1
sport=2
```

Important:

Do NOT rely only on percentages.

Compare exact GameIds.

---

# 57. SPORT-SPECIFIC SET COMPARISON

Calculate:

```text
Soccer HAR sport=1 set
vs
Basketball HAR sport=1 set
```

Show:

```text
shared
Soccer-page-only
Basketball-page-only
Jaccard
```

Then:

```text
Soccer HAR sport=2 set
vs
Basketball HAR sport=2 set
```

Show the same metrics.

This directly tests whether selected sport changes the corresponding sport subset.

---

# 58. CH DISTRIBUTION

For both HARs calculate:

```text
ch → distinct GameId count
ch → gamefull request count
```

Compare:

```text
shared ch
Soccer-only ch
Basketball-only ch
```

Questions:

```text
Do selected sport tabs introduce distinct ch groups?

Do exactly the same ch groups remain active?

Are differences concentrated in one sport?

Does ch appear more related to fixture grouping than tab selection?
```

Do not assign exact semantic meaning to `ch` without sufficient evidence.

---

# 59. PREMATCH/GAMES → GAMEFULL CORRELATION

For every `gamefull` request:

Find the latest prior decoded:

```text
prematch/games UpdateList
```

entry for SAME GameId in the SAME HAR.

Calculate:

```text
gamefull timestamp
-
notification timestamp
```

Generate buckets:

```text
≤0.5 sec
≤1 sec
≤1.5 sec
≤2 sec
≤3 sec
≤5 sec
>5 sec
```

Do separately for Soccer and Basketball.

Previous benchmark:

Soccer:

```text
103 / 103 within 5 sec
```

Previous Basketball:

```text
133 / 138 within 5 sec
138 / 138 had an earlier notification
```

Check whether parallel runs reproduce the relationship.

---

# 60. CROSS-BROWSER NOTIFICATION TIMING

Because sessions run simultaneously, compare timestamps of same notification GameIds between HARs.

Question:

```text
Do both clients receive prematch/games changes
at approximately the same time?
```

If yes, that further supports:

```text
prematch/games = shared/global invalidation feed
```

Potentially calculate same-GameId occurrence differences such as:

```text
median timestamp difference
p95 difference
```

when practical.

---

# 61. FETCH BURSTS

Detect cases:

```text
one notification
→ multiple same-GameId gamefull requests
```

Record:

```text
maximum burst size
examples
burst duration
```

Compare whether Soccer and Basketball sessions behave similarly.

Do NOT force 1 notification = 1 fetch semantics.

---

# 62. REQUEST CADENCE

Normalize for overlap duration:

```text
prematch/games requests/minute
gamefull requests/minute
distinct fetched GameIds/minute
```

Compare both browsers.

Since captures are simultaneous, large cadence differences may themselves be useful.

---

# 63. GAMEFULL RESPONSE STATE COMPARISON ACROSS BROWSERS

For GameIds fetched by BOTH browsers around similar times, compare authoritative snapshots where practical.

At minimum inspect:

```text
up
pc
mc
market count
selection count
```

Question:

```text
Do both sessions receive identical authoritative state
for the same GameId at roughly the same time?
```

Expected answer should probably be yes, but verify.

This also helps distinguish:

```text
different relevance decision
```

from:

```text
different underlying data source/state
```

---

# 64. FIVE ch=8015 SOCCER FIXTURES

If still present during the new capture, explicitly check:

```text
76433706
76433710
76433712
76433719
76473552
```

However they may have already started/ended by the time of the next experiment.

If absent due lifecycle timing, do NOT treat absence as tab evidence.

Use whatever active shared fixtures exist at the time of the parallel test.

---

# 65. PARALLEL EXPERIMENT — POSSIBLE OUTCOME A

If:

```text
notification streams nearly identical
AND
gamefull GameId sets nearly identical
AND
high Jaccard
AND
sport-specific sets nearly identical
```

then strong evidence:

```text
selected sport tab is mostly a presentation/UI filter
```

Potential model:

```text
/upcoming page owns broad active dataset
selected tab mostly controls rendering/filtering
```

Still avoid claiming literally zero influence unless evidence supports that.

---

# 66. POSSIBLE OUTCOME B

If:

```text
same notification stream
BUT

many sport=1 IDs fetched only in Soccer session
AND
many sport=2 IDs fetched only in Basketball session
```

then:

```text
selected sport materially influences
the frontend relevance filter
```

Likely model:

```text
shared global invalidation stream
+
client-local sport relevance filtering
+
gamefull only for relevant IDs
```

This would be a major protocol/frontend discovery.

---

# 67. POSSIBLE OUTCOME C

If:

```text
large common gamefull core
+
meaningful sport-specific differences
```

then likely model:

```text
shared page-level core
+
selected-tab-specific relevant subset
```

Quantify:

```text
core percentage
sport-specific delta percentage
```

This may be the most likely nuanced model.

---

# 68. POSSIBLE OUTCOME D

If notification streams themselves differ substantially between the simultaneous sessions:

Do NOT immediately conclude tab filtering happens after notification.

Instead investigate possibility that:

```text
subscription/session state changes prematch/games feed itself
```

or:

```text
cache request/notification scheduling differs
```

This would be unexpected given current global-stream evidence, but must be measured rather than assumed.

---

# 69. REQUIRED ANSWERS AFTER NEW PARALLEL ANALYSIS

Explicitly answer:

```text
1. How much wall-clock overlap did the HARs have?

2. Did both sessions receive essentially the same
   prematch/games notification universe?

3. What is the notification-set Jaccard?

4. What is the gamefull relevant-set Jaccard?

5. Which GameIds were fetched by both?

6. Which were Soccer-session-only?

7. Which were Basketball-session-only?

8. For asymmetric GameIds, was the SAME notification
   seen in both browser sessions?

9. Are asymmetric fetches correlated with sport=1 / sport=2?

10. Does Soccer selection increase sport=1 relevance?

11. Does Basketball selection increase sport=2 relevance?

12. Are Soccer fixtures still refreshed in Basketball session?

13. Are Basketball fixtures still refreshed in Soccer session?

14. How does ch distribution differ?

15. Does prematch/games → gamefull correlation remain?

16. Does gameall remain absent?

17. Is the selected tab best classified as:
    - presentation/UI filter
    - partial relevance influence
    - strong relevance/subscription filter
```

Do not answer #17 impressionistically.

Base it on the quantitative comparison.

---

# 70. EXPECTED OUTPUT FORMAT AFTER NEW HAR ANALYSIS

Start with a compact table:

```text
                              Soccer       Basketball
------------------------------------------------------
Capture start                 ...
Capture end                   ...
Duration                      ...
Common overlap                ...

HAR entries                   ...
prematch/games requests       ...
UpdateList entries            ...
distinct notified GameIds     ...
gamefull calls                ...
distinct gamefull GameIds     ...
gameall calls                 ...

sport=1 distinct              ...
sport=2 distinct              ...
sport=1 calls                 ...
sport=2 calls                 ...
distinct ch                   ...
```

Then:

## Notification universe

```text
Soccer notifications      = X
Basketball notifications  = Y
Shared                    = Z
Soccer-only               = ...
Basketball-only           = ...
Jaccard                   = ...%
```

Then:

## Gamefull relevant universe

```text
Soccer GameIds      = X
Basketball GameIds  = Y
Shared              = Z
Soccer-only         = ...
Basketball-only     = ...
Jaccard             = ...%
```

Then exact asymmetric IDs.

Then:

## Same notification / different fetch decisions

This should receive special emphasis.

Then:

## Sport-specific comparison

Then:

## ch comparison

Then:

## notification→gamefull timing

Then:

## PROVEN FROM PARALLEL HARS

## STRONG EVIDENCE

## STILL UNKNOWN

Finally recommend at most:

```text
ONE next experiment
```

and only if another experiment is actually needed.

---

# 71. VERY IMPORTANT ANALYTICAL PRINCIPLE FOR NEXT CHAT

The strongest evidence in the new parallel experiment will NOT simply be:

```text
GameId appears in Soccer HAR
but not Basketball HAR
```

The strongest case is:

```text
same GameId
+
same prematch/games invalidation observed in BOTH sessions
+
Soccer browser does gamefull
+
Basketball browser does NOT

or vice versa
```

That directly isolates the frontend's relevance decision.

This is the central analysis target.

---

# 72. CURRENT LIKELY COLLECTOR ARCHITECTURE SHIFT

Current collector roughly does:

```text
prematch/games
→ gameall
→ partial merge
→ pc check
→ periodic gamefull reconciliation
```

Browser evidence increasingly looks like:

```text
prematch/games
→ check relevant GameId
→ gamefull
→ replace with authoritative state
```

This may eventually justify a major collector redesign.

BUT:

```text
DO NOT CHANGE IT YET
```

First complete parallel sport-tab experiment.

Then determine what GameId universe the collector itself should maintain.

---

# 73. LARGER UNRESOLVED COLLECTOR DESIGN QUESTION

Even if browser relevance rule becomes known:

Collector needs potentially broader coverage.

Ultimate question:

```text
How should a headless collector discover and maintain
the full prematch fixture universe?
```

Possible future investigation areas:

```text
bootstrap/list endpoints
prematch/header
page/category discovery
competition grouping
fixture expiration
kickoff transition
prematch → live mapping
```

But NONE of these should distract from the current parallel experiment.

---

# 74. CURRENT KNOWLEDGE MATRIX

```text
MQTT connection                             ✅ PROVEN
reconnect/resubscribe                       ✅ PROVEN
unsubscribe                                 ✅ PROVEN
cache indirection                           ✅ PROVEN

live exact GameId topic                     ✅ PROVEN
live diff                                   ✅ PROVEN
match-end handling                          ✅ PROVEN

prematch/header                             ✅ PROVEN
prematch/games                              ✅ PROVEN
prematch/markets                            ✅ PROVEN

gamefull endpoint                           ✅ PROVEN
gameall endpoint                            ✅ PROVEN

gamefull authoritative                      ✅ PROVEN
gameall partial                             ✅ PROVEN

gameall complete logical delta              ❌ DISPROVEN
missing gameall market = unchanged          ❌ DISPROVEN
pc equality = exact-state equality          ❌ DISPROVEN

prematch/games global/broad stream          ✅ VERY STRONG

prematch/games → same-ID gamefull           ✅ VERY STRONG

all notification IDs → gamefull             ❌ FALSE

selected Soccer → only Soccer refresh       ❌ DISPROVEN

selected Basketball → no Soccer refresh     ❌ DISPROVEN

selected sport primary exclusive selector   ❌ DOES NOT FIT EVIDENCE

selected sport zero influence               ❓ UNKNOWN

selected sport partial influence            ❓ CURRENT EXPERIMENT

ch exact semantics                          ❓ UNKNOWN

DeleteList exact semantics                  ❓ UNKNOWN

context ID 28 exact semantics               ❓ UNKNOWN

gameall role outside realtime chain         ❓ UNKNOWN

collector full relevant universe            ❓ UNKNOWN
```

---

# 75. MOST IMPORTANT CURRENT QUESTION

Do not get distracted by implementation.

Right now answer:

```text
When two /prematch/upcoming browser sessions run
at the SAME TIME,

one with Soccer selected
and one with Basketball selected,

do they receive the same prematch/games invalidations
but make different gamefull fetch decisions?
```

And if yes:

```text
Are those different decisions systematically explained
by the selected sport?
```

That is the purpose of the two NEW HAR files.

---

# 76. NEW CHAT OPENING INSTRUCTION

After this handoff is pasted, the user will upload:

```text
soccer.har
basketball.har
```

They are simultaneous/parallel captures.

Do not ask the user what to do.

Do not ask them to upload again if attached.

Do not modify the collector.

Immediately analyze both files.

Primary objective:

```text
compare SAME-TIME notification input
against SAME-TIME gamefull fetch output
```

and determine whether:

```text
selected sport
```

actually participates in frontend GameId relevance filtering.

This parallel experiment is more important than the previous sequential Soccer/Basketball comparison because it removes fixture lifecycle/time progression as the major confound.
