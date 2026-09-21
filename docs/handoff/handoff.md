# MyStake Collector — Complete Handoff / Continuation Context

Bu doküman `mystake-collector` projesine yeni bir ChatGPT session’ında kaldığımız yerden eksiksiz devam edebilmek için hazırlanmıştır.

Amaç:
- Şimdiye kadar yaptığımız reverse-engineering çalışmalarını kaybetmemek.
- Kanıtlanmış ve henüz kanıtlanmamış protocol davranışlarını birbirinden ayırmak.
- Mevcut çalışan kodu bozmamak.
- Aynı deneyleri gereksiz yere tekrar etmemek.
- Bir sonraki hedefe doğrudan devam etmek.

Ana çalışma prensibi:

```text
observe
→ experiment
→ prove
→ only then productionize
```

Tahminle protocol semantics uydurma.

Gereksiz refactor yapma.

Kullanıcı:
- macOS kullanıyor.
- PyCharm kullanıyor.
- Terminalde `uv` kullanıyor.
- Chrome DevTools ile network/socket reverse engineering yapıyor.
- Adım adım ilerlemek istiyor.
- Kod değişikliklerinde exact filename / complete file / exact command tercih ediyor.
- Terminal çıktısını gönderiyor, sonra sonraki adıma geçiliyor.

---

# 1. PROJECT

Project:

```text
mystake-collector
```

Ana hedef:

MyStake’tan browser bağımsız şekilde şu verileri toplamak:

```text
fixture discovery
prematch state
prematch markets
prematch selections
prematch odds
live state
live markets
live odds
live score
match lifecycle
match end
cleanup
```

Sadece live maçlar takip edilmeyecek.

Hedef full lifecycle:

```text
fixture appears
→ PREMATCH
→ prematch markets / selections / odds updates
→ kickoff
→ LIVE
→ live score / markets / odds / state
→ MATCH_ENDED
→ final state
→ cleanup
```

---

# 2. DEVELOPMENT ENVIRONMENT

Environment:

```text
macOS
PyCharm
Python 3.13
uv
```

Project root:

```text
mystake-collector/
```

Temel komutlar:

```bash
uv run pytest
```

```bash
uv run python main.py
```

```bash
uv run python inspect_prematch_games.py
```

```bash
uv run python -m py_compile inspect_prematch_games.py
```

Regression baseline:

```text
47 passed
```

Bu baseline korunmalı.

---

# 3. CURRENT PROJECT STRUCTURE

Mevcut structure:

```text
mystake-collector/
├── .venv/
│
├── docs/
│   ├── handoff/
│   │   ├── baglam.md
│   │   └── handoff.md
│   └── product/
│
├── mystake/
│   ├── events/
│   │   ├── __init__.py
│   │   ├── mapper.py
│   │   └── models.py
│   │
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── cache_decoder.py
│   │   ├── live_snapshot_diff.py
│   │   └── notification_processor.py
│   │
│   ├── sources/
│   │   ├── cache/
│   │   │   ├── __init__.py
│   │   │   └── client.py
│   │   │
│   │   └── mqtt/
│   │       ├── __init__.py
│   │       ├── client.py
│   │       ├── message.py
│   │       └── protocol.py
│   │
│   ├── __init__.py
│   └── config.py
│
├── tests/
│
├── .gitignore
├── AGENTS.md
├── CLAUDE.md
├── inspect_prematch_games.py
├── main.py
├── pyproject.toml
└── uv.lock
```

---

# 4. ARCHITECTURE RULE

Katmanlar kabaca:

```text
sources/
    transport / protocol

pipeline/
    decode / transform / diff

events/
    semantic events

future:
    lifecycle/
    storage/
    prematch state modules
```

Önemli kural:

`mystake/sources/mqtt/client.py`

transport/protocol seviyesinde kalmalı.

Şunları MQTT client içine koyma:

```text
domain diff
fixture lifecycle
database writes
odds normalization
semantic event generation
business logic
```

---

# 5. MQTT CONNECTION — PROVEN

WebSocket endpoint:

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

Çalışan sequence:

```text
CONNECT
→ CONNACK
→ SUBSCRIBE
→ SUBACK
→ PUBLISH
→ PINGREQ / PINGRESP
```

Reconnect/resubscribe de gerçek broker üzerinde doğrulandı.

---

# 6. MQTT RECONNECT — PROVEN

Gerçek testte:

```text
connection lost
→ reconnect
→ CONNACK
→ restore subscriptions
→ continue receiving
```

çalıştı.

---

# 7. MQTT UNSUBSCRIBE — PROVEN

Gerçek broker üzerinde:

```text
UNSUBSCRIBE
→ UNSUBACK
```

doğrulandı.

Önemli implementation detayı:

Broker bazen `UNSUBACK` gelmeden önce yeni `PUBLISH` gönderebiliyor.

Possible sequence:

```text
PUBLISH
PUBLISH
UNSUBACK
```

Bu nedenle pending packet queue korunmalı.

---

# 8. LIVE MQTT TOPIC — PROVEN

Exact live topic:

```text
live/gamenew/{GameId}
```

Örnek:

```text
live/gamenew/74679515
```

Exact subscribe çalışıyor.

Wildcard:

```text
live/gamenew/#
```

test edildi ve broker:

```text
SUBACK 0x80
```

döndürdü.

Yani wildcard reddediliyor.

---

# 9. MQTT CACHE MODEL — PROVEN

MQTT `PUBLISH` çoğu zaman data’yı doğrudan taşımıyor.

Payload:

```text
cache:https://...
```

Pipeline:

```text
MQTT PUBLISH
→ cache URL
→ HTTP GET
→ base64 decode
→ optional gzip
→ JSON decode
```

Bu pipeline implement edildi.

---

# 10. NotificationProcessor DETAIL

`NotificationProcessor.process(message)` doğrudan dict dönmüyor.

Return:

```text
ProcessedNotification
```

Decoded payload:

```python
processed.data
```

üzerinden alınmalı.

---

# 11. LIVE STATE — PROVEN

Live snapshot içinde gözlenen alanlar:

```text
gmk
TimeLines
Match
mk
```

Live snapshot diff implement edildi.

Semantic live events üretilebiliyor.

---

# 12. LIVE SEMANTICS

Gözlenen:

```text
visible=false
```

tek başına bütün market suspended demek değil.

```text
LiveBetStatus=false
```

tek başına match ended demek değil.

Football için güçlü match-end pattern:

```text
Status == 3
BetStatus == 0
EventStatus == 40
```

---

# 13. REAL LIVE MATCH-END TEST

Gerçek GameId:

```text
75297868
```

Maç yaklaşık 88. dakikadan bitişe kadar izlendi.

Kanıtlanan lifecycle:

```text
subscribe
→ live updates
→ match-end detection
→ final state
→ cleanup
```

---

# 14. PREMATCH GLOBAL MQTT TOPICS — PROVEN

Chrome DevTools ve Python reverse engineering sonucu şu üç topic doğrulandı:

```text
prematch/header
prematch/games
prematch/markets
```

Hepsi için:
- SUBSCRIBE görüldü.
- Server → client PUBLISH görüldü.

---

# 15. MQTT SUBSCRIBE FRAMES — PROVEN

`prematch/header`:

```text
82 14 00 02 00 0F ... prematch/header ... 00
```

`prematch/games`:

```text
82 13 00 04 00 0E ... prematch/games ... 00
```

`prematch/markets`:

```text
82 15 00 06 00 10 ... prematch/markets ... 00
```

Decode:

```text
82 = MQTT SUBSCRIBE
00 xx = packet id
00 xx = topic length
topic string
00 = requested QoS 0
```

---

# 16. PREMATCH PUBLISH FRAMES — PROVEN

`prematch/games` PUBLISH örneği:

```text
30 ...
00 0E
prematch/games
cache:https://...
```

`prematch/header` PUBLISH örneği:

```text
prematch/header
cache:https://wss-eu-uk1.ws-amazon.com/api/cache/get?key=prematch/header
```

`prematch/markets` PUBLISH örneği:

```text
prematch/markets
cache:https://wss-eu-uk1.ws-amazon.com/api/cache/get?key=prematch/markets
```

---

# 17. PREMATCH/GAMES CACHE

Cache endpoint:

```text
https://wss-eu-uk1.ws-amazon.com/api/cache/get?key=prematch/games
```

Decoded payload:

```json
{
  "UpdateList": [
    {
      "GameId": 12345678,
      "UpdateTimeStamp": 1789936601183
    }
  ],
  "DeleteList": []
}
```

`UpdateList` full fixture universe değildir.

Daha çok:

```text
change / update notification stream
```

gibi davranır.

Aynı notification tekrar gelebilir.

Exact duplicate suppression implement edildi.

---

# 18. PREMATCH/GAMES DUPLICATES

Exact duplicate messages sık geliyor.

Collector bunları skip ediyor.

Bu behavior korunmalı.

---

# 19. UpdateTimeStamp

`UpdateTimeStamp` bazen:

```text
13 digit
```

bazen:

```text
10 digit
```

olarak görüldü.

Exact semantics kesin değil.

Monotonic version kabul etme.

---

# 20. PREMATCH/MARKETS — IMPORTANT DISCOVERY

MQTT topic:

```text
prematch/markets
```

publish edildiğinde payload:

```text
cache:https://wss-eu-uk1.ws-amazon.com/api/cache/get?key=prematch/markets
```

Cache endpoint:

```text
https://wss-eu-uk1.ws-amazon.com/api/cache/get?key=prematch/markets
```

örnek response:

```text
"MTc4OTk3ODUwMw=="
```

Base64 decode:

```text
1789978503
```

Daha sonra:

```text
"MTc4OTk3OTEwMw=="
```

decode:

```text
1789979103
```

Fark:

```text
600 seconds
```

yani tam:

```text
10 minutes
```

Güçlü hipotez:

```text
prematch/markets
→ actual market data değil
→ timestamp / version / invalidation marker gibi davranıyor
```

Bu henüz formal semantic olarak kesin isimlendirilmemeli.

Ama actual odds payload olmadığı açık.

---

# 21. PREMATCH/HEADER

Topic:

```text
prematch/header
```

Cache endpoint:

```text
https://wss-eu-uk1.ws-amazon.com/api/cache/get?key=prematch/header
```

Exact content/semantic henüz derin analiz edilmedi.

---

# 22. GETPREMATCHGAMEFULL — PROVEN

Endpoint:

```text
https://analytics-sp.googleserv.tech/api/prematch/getprematchgamefull/28/{GameId}
```

Pattern:

```text
/api/prematch/getprematchgamefull/{CONTEXT_ID}/{GAME_ID}
```

Şimdilik:

```python
PREMATCH_CONTEXT_ID = 28
```

`28` sport id değildir.

Exact anlamı bilinmiyor.

---

# 23. GAMEFULL RESPONSE FORMAT

Outer:

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

`gamefull` authoritative full snapshot gibi davranıyor.

---

# 24. PREMATCH GAME STRUCTURE

Observed fields:

```text
id
ch
t1
t2
sti
st
s
vis
istop
istopbaner
pc
mc
sport
region
stunix
up
bid
gmrc
hasstream
ev
```

Kısa field’lara kanıtsız semantic isim verme.

---

# 25. PREMATCH MARKET STRUCTURE

`game.ev`:

```text
marketId
  selectionId
    pos
    coef
    res
    lock
    h
    hism
    p1
    pl
```

---

# 26. GETPREMATCHGAMEALL — PROVEN

Endpoint:

```text
https://analytics-sp.googleserv.tech/api/prematch/getprematchgameall/fr/28/?games=,75433979
```

Browser ayrıca `en` language variant kullanıyor:

```text
https://analytics-sp.googleserv.tech/api/prematch/getprematchgameall/en/28/?games=,...
```

Pattern:

```text
/api/prematch/getprematchgameall/{LANGUAGE}/{CONTEXT_ID}/?games=,{GAME_IDS}
```

Nested JSON strings içeriyor.

Top-level:

```text
game
outrights
companyPrice
teams
```

---

# 27. GAMEALL BATCH SUPPORT — PROVEN

Multiple GameIds tek request içinde çalışıyor:

```text
?games=,75433979,75747467
```

---

# 28. TEAM ID → NAME

Mapping:

```text
game.t1 / game.t2
→ teams[].ID
→ teams[].Name
```

çalışıyor.

---

# 29. MULTI-SPORT PREMATCH STREAM

`prematch/games` multiple sports taşıyor.

Observed IDs:

```text
1
2
3
5
15
22
26
63
66
73
101
...
```

Bazıları observation ile biliniyor:

```text
1 → football-looking
3 → baseball example
5 → tennis examples
```

Bilinmeyenleri tahmin etme.

---

# 30. GAMEALL VS GAMEFULL — CRITICAL PROOF

Fixture:

```text
75747467
Flamengo RJ vs Red Bull Bragantino SP
```

Observed:

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

Strict subset:

```text
gameall markets ⊂ gamefull markets
gameall selections ⊂ gamefull selections
```

Conclusion:

```text
getprematchgameall = PARTIAL payload
getprematchgamefull = FULL authoritative snapshot
```

---

# 31. ORIGINAL MERGE MODEL

İlk merge selection-by-selection upsert idi.

Bu gerçek trafikte yetersiz çıktı.

Daha iyi model:

```text
market absent from delta
→ preserve local market

market present in delta
→ replace entire local market
```

Implementation:

```python
for market_id, delta_selections in delta["ev"].items():
    merged["ev"][market_id] = deepcopy(delta_selections)
```

---

# 32. TOP-LEVEL SCALAR MERGE

Delta içinde gelen scalar fields local state üzerine overwrite ediliyor.

`ev` separately market-level işleniyor.

---

# 33. pc CHECK

`pc` ile total selection count arasında güçlü relation var.

Örnek:

```text
gamefull pc = 6793
count(full ev selections) = 6793
```

Bu nedenle merge sonrası:

```text
local selection count != pc
→ suspect state
→ gamefull resync
```

mantığı eklendi.

---

# 34. COUNT-MISMATCH RESYNC — PROVEN

Örnek:

```text
local=402
server_pc=407
```

gamefull sonrası:

```text
407
```

geldi.

Bu safety mechanism işe yarıyor.

---

# 35. REAL PREMATCH ODDS CHANGES — PROVEN

Gerçek traffic:

```text
coef: 1.82 -> 1.90
coef: 1.92 -> 1.84
```

başka:

```text
2.17 -> 2.12
1.59 -> 1.62
```

Collector gerçek odds değişimini yakalıyor.

---

# 36. REAL SELECTION ADDITIONS — PROVEN

Örnek:

```text
pc: 16 -> 18
```

market:

```text
2117
```

added:

```text
11606876691
11606876692
```

---

# 37. DELETE LIST

Bugüne kadar çoğunlukla:

```text
DeleteList = []
```

Exact semantics unknown.

Gelirse doğrudan delete etme.

Önce logla ve kanıtla.

---

# 38. LOCK CHANGES

Soak testlerde:

```text
Lock changes = 0
```

Bu sadece observation.

Never changes anlamına gelmez.

---

# 39. FIRST 5-MINUTE SOAK RESULT

```text
Notifications            : 185
Duplicate notifications  : 81
Delta batches            : 102
Delta games              : 447
Full bootstraps          : 189
Delta merges             : 258
Count matches            : 249
Count mismatches         : 9
Full resyncs             : 9
Logical changes          : 108
No-op updates             : 150
Price changes            : 475
Lock changes             : 0
Added markets            : 0
Removed markets          : 108
Added selections         : 16
Removed selections       : 54
Direct merge count-match : 96.51%
```

---

# 40. SAMPLED FULL VALIDATION

Question:

```text
count match == exact state ?
```

Bunu test etmek için sampled gamefull validation eklendi.

İlk sample exact match çıktı.

Daha sonra critical mismatch’ler geldi.

---

# 41. SAME-COUNT HIDDEN DIVERGENCE — PROVEN

Example:

```text
GameId=76295715
Madla vs Haugesund 2
```

Observed:

```text
merged up == full up
local pc == full pc
local markets == full markets
local selections == full selections
```

ama:

```text
local coef != full coef
```

birçok selection için görüldü.

---

# 42. SECOND SAME-COUNT MISMATCH

Example:

```text
GameId=76436060
Sychra, Martin vs Kasnik, Sebastian
```

Counts same.

`up` same.

Ama odds farklı.

---

# 43. STABLE MISMATCH VALIDATION

Validator enhanced:

```text
local != full1
fetch full2 immediately
compare full1 vs full2
```

Classification:

```text
FULL1 == FULL2
LOCAL != FULL
→ STABLE_MISMATCH
```

---

# 44. DECISIVE STABLE_MISMATCH

Example:

```text
GameId=76447616
Aurora Zantedeschi vs Lombardini, Marta
```

Observed:

```text
merged up == full1 up == full2 up
local != full2
full1 == full2
```

Conclusion:

```text
not a race
```

Mismatch markets all:

```text
present_in_current_delta=False
```

Ama authoritative full’da odds değişmişti.

---

# 45. CRITICAL PREMATCH RULE

Proven:

```text
market missing from gameall
≠ removed
```

ve:

```text
market missing from gameall
≠ unchanged
```

Bu çok önemli.

---

# 46. SELECTION IDENTITY CAN CHANGE WITHOUT COUNT CHANGE

Example market:

```text
580
```

Local IDs:

```text
11609165868
11609165869
```

Full IDs:

```text
11609158205
11609158206
```

Count aynı ama IDs farklı.

Conclusion:

```text
gameall is NOT a complete logical delta
```

---

# 47. CURRENT GAMEALL MODEL

Best evidence-based model:

```text
gameall
=
partial view / partial update
```

Ama:

```text
NOT authoritative full snapshot
NOT complete logical delta
```

---

# 48. PERIODIC AUTHORITATIVE RECONCILIATION

Experimental threshold:

```python
RECONCILE_EVERY_GAME_MERGES = 10
```

Flow:

```text
first seen
→ gamefull bootstrap

known game
→ gameall merge

every 10 same-GameId merges
→ gamefull reconciliation
```

If difference stable:

```text
replace local with authoritative full
```

---

# 49. REAL RECONCILIATION REPAIR — PROVEN

Game:

```text
76448598
Fernandez, Bruno vs Farjat, Tomas
```

Observed stable authoritative difference.

Repair:

```text
local state replaced with newest gamefull
```

---

# 50. LATEST 10-MINUTE SOAK RESULT

Critical checkpoint:

```text
Notifications            : 522
Duplicate notifications  : 174
Delta batches            : 335
Delta games              : 1333
Full bootstraps          : 350
Delta merges             : 983
Count matches            : 950
Count mismatches         : 33
Full resyncs             : 33
Reconciliation checks    : 11
Reconciliation matches   : 1
Reconciliation repairs   : 10
Reconciliation races     : 0
Sampled validations      : 0
Sampled exact matches    : 0
Sampled mismatches       : 0
Sampled races            : 0
Stable mismatches        : 0
Hidden same-up races     : 0
Unclassified mismatches  : 0
Logical changes          : 485
No-op updates             : 498
Price changes            : 2966
Lock changes             : 0
Added markets            : 261
Removed markets          : 109
Added selections         : 145
Removed selections       : 148

Direct merge count-match : 96.64%
```

Reconciliation:

```text
checks = 11
matches = 1
repairs = 10
```

Repair rate:

```text
~90.9%
```

Conclusion:

```text
pc match
≠ exact state correctness
```

---

# 51. IMPORTANT ARCHITECTURE CONCLUSION

Do NOT simply solve this by lowering:

```text
RECONCILE_EVERY_GAME_MERGES
10 → 5 → 1
```

That would become brute-force full snapshot polling.

`gamefull` can be hundreds of KB.

Need protocol correctness first.

---

# 52. BROWSER REVERSE ENGINEERING — RECENT DISCOVERY

We moved to Chrome DevTools to find how MyStake frontend updates prematch state.

Confirmed global prematch topics:

```text
prematch/header
prematch/games
prematch/markets
```

No GameId-specific prematch MQTT topic has been proven so far.

---

# 53. PREMATCH/MARKETS DOES NOT APPEAR TO BE ACTUAL ODDS PAYLOAD

Its cache result is a base64 timestamp-like value.

Observed exact +600 second movement.

So strongest current interpretation:

```text
prematch/markets
→ 10-minute marker / version / invalidation signal
```

Do NOT call it exact semantic until more evidence.

---

# 54. FETCH/XHR OBSERVATIONS

Browser uses:

```text
getprematchgameall
getprematchgamefull
```

Endpoints.

Examples seen:

```text
https://analytics-sp.googleserv.tech/api/prematch/getprematchgameall/en/28/?games=,76446999
```

```text
https://analytics-sp.googleserv.tech/api/prematch/getprematchgamefull/28/76446999
```

Initiator often:

```text
helpers.js?v=2.3.7:1
```

---

# 55. IMPORTANT BROWSER TEST PROBLEM

Several test matches were only 4–5 minutes from kickoff.

This caused observations to become mixed with:

```text
prematch → kickoff → live transition
```

So some `gamefull` calls seen after kickoff cannot safely be interpreted as normal prematch update behavior.

Need a cleaner test window next time.

---

# 56. TEST CASE — GameId 76447253

URL:

```text
https://mystake.com/en/sportsbook/prematch/match/76447253
```

Browser had this match open.

Collector output:

```text
GameId=76447253 timestamp=1789980302158 known=False
2026-09-21 11:45:02,815 INFO root - Fetching prematch DELTA batch games=1
2026-09-21 11:45:03,094 INFO httpx - HTTP Request: GET https://analytics-sp.googleserv.tech/api/prematch/getprematchgameall/fr/28/?games=,76447253 "HTTP/1.1 200 OK"
```

Delta:

```text
delta markets    : 0
delta selections : 0
delta up         : 1789980302
delta mc         : 0
delta pc         : 0
```

Collector then did:

```text
STATE ACTION: FIRST SEEN -> FULL BOOTSTRAP
```

and:

```text
2026-09-21 11:45:03,372 INFO httpx - HTTP Request: GET https://analytics-sp.googleserv.tech/api/prematch/getprematchgamefull/28/76447253
```

Game:

```text
Feyenoord Rotterdam (Snake)
vs
Galatasaray (Scorpion)
```

Fields:

```text
Start           : 2026-09-21T08:45:00
Sport           : 1
Region          : 1205
Champ/Ch        : 76163
Local markets   : 0
Local selections: 0
Server mc       : 0
Server pc       : 0
Updated         : 1789980302
Visible         : False
Has stream      : False
```

Important timezone interpretation:

```text
Start = 08:45 UTC
local terminal time = 11:45 +03
```

So match scheduled kickoff was exactly:

```text
11:45 local
```

UpdateList arrived around:

```text
11:45:02
```

Therefore this event was almost certainly kickoff/lifecycle-related, not a clean pre-kickoff odds update.

---

# 57. IMPORTANT LIFECYCLE CLUE FROM 76447253

At kickoff:

```text
prematch/games
→ GameId=76447253
```

then gameall returned:

```text
markets = 0
selections = 0
mc = 0
pc = 0
```

gamefull also showed:

```text
markets = 0
selections = 0
visible = false
```

This is potentially very important for future prematch → live transition logic.

Do not lose this observation.

Potential lifecycle model to test later:

```text
prematch fixture reaches kickoff
→ prematch/games UpdateList
→ prematch snapshot emptied / visibility false
→ live topic may become active
```

Not fully proven yet, but very promising.

---

# 58. CHROME SIDE FOR 76447253

Chrome showed repeated XHR rows:

```text
https://analytics-sp.googleserv.tech/api/prematch/getprematchgamefull/28/76447253
```

Initiator:

```text
helpers.js?v=2.3.7:1
```

However these calls were at/after kickoff.

Therefore:

```text
DO NOT conclude normal prematch polling from this test.
```

Could be lifecycle transition behavior.

---

# 59. CURRENT BEST PREMATCH MODEL

Evidence-based:

```text
prematch/header
→ global metadata/update channel

prematch/games
→ GameId change notifications / lifecycle signals

prematch/markets
→ global 10-minute marker/version-like signal

getprematchgameall
→ partial state / partial update

getprematchgamefull
→ authoritative snapshot
```

Open question:

```text
When exactly does browser call gamefull while match is still cleanly PREMATCH?
```

---

# 60. CURRENT MAIN UNRESOLVED QUESTION

Need to prove frontend behavior for a match still safely in PREMATCH state.

Specifically:

```text
prematch/games UpdateList contains open GameId
→ does browser call getprematchgamefull?
→ does browser call getprematchgameall?
→ both?
→ only on some events?
```

---

# 61. NEXT TEST MUST AVOID KICKOFF CONTAMINATION

Do NOT use a match starting in 4–5 minutes.

Use:

```text
15–30 minutes before kickoff
```

preferably one with active odds.

Reason:

Need enough observation window to catch a genuine prematch odds/state update before lifecycle transition.

---

# 62. NEXT EXPERIMENT DESIGN

Choose one match:

```text
GameId = X
kickoff at least 15–30 minutes away
```

Run simultaneously:

Terminal:

```bash
cd ~/Desktop/Projects/mystake-collector
uv run python inspect_prematch_games.py
```

Chrome:

```text
DevTools
→ Network
→ Fetch/XHR
→ Preserve log ON
→ filter = X
→ clear list
```

Also optionally keep Socket in another DevTools window.

Wait until collector prints:

```text
GameId=X
```

inside:

```text
prematch/games UpdateList
```

Then compare browser network events.

---

# 63. DO NOT COMPARE TIMESTAMPS MANUALLY IN DEVTOOLS

Chrome Network table does not show convenient absolute timestamps.

Manual visual comparison became confusing.

Better next approach:

```text
EXPORT HAR
```

HAR contains exact:

```text
startedDateTime
request.url
timings
duration
```

This can be correlated with collector log timestamps.

---

# 64. RECOMMENDED NEXT TECHNICAL STEP — HAR CORRELATION

For clean prematch test:

```text
1. Open match 15–30 minutes before kickoff
2. Clear Network
3. Preserve log ON
4. Record traffic
5. Let collector run
6. Wait for GameId in UpdateList
7. Export Network as HAR
8. Compare HAR timestamps to terminal logs
```

Goal output:

```text
11:32:14.527
prematch/games → GameId=X

11:32:14.641
browser → getprematchgamefull/28/X

difference = 114 ms
```

or:

```text
no corresponding gamefull
```

This will prove trigger relationship much more reliably.

---

# 65. POSSIBLE AUTOMATION LATER

Once we have a HAR file, create a small parser script, possibly:

```text
analyze_har_prematch.py
```

Input:

```text
HAR file
GameId
```

Output:

```text
timestamp
URL
initiator if available
duration
```

Then compare with collector logs.

Do this only after getting a clean HAR sample.

---

# 66. IMPORTANT: DO NOT MODIFY CORE PYTHON YET

Current objective is still protocol discovery.

Do NOT yet:

```text
refactor inspect_prematch_games.py into production modules
add DB
add Redis
add Kafka/RabbitMQ
rewrite in Java
change reconciliation policy heavily
```

First solve frontend update behavior.

---

# 67. CURRENT MOST IMPORTANT PROVEN FACTS

Keep these front and center:

```text
gameall is partial
```

```text
gameall is not a complete logical delta
```

```text
market missing from gameall
≠ removed
```

```text
market missing from gameall
≠ unchanged
```

```text
pc match
≠ exact state correctness
```

```text
gamefull is authoritative
```

```text
prematch/games is a real update signal
```

```text
prematch/markets exists but appears to be a 10-minute marker
```

```text
kickoff can trigger prematch/games with empty/hidden prematch state
```

---

# 68. CURRENT MILESTONE STATUS

```text
MQTT connection                          ✅
CONNECT / CONNACK                        ✅
SUBSCRIBE / SUBACK                       ✅
PUBLISH                                  ✅
PING                                     ✅
reconnect                                ✅
resubscribe                              ✅
unsubscribe                              ✅

cache indirection                        ✅
cache fetch                              ✅
base64/gzip/json decode                  ✅

live/gamenew/{GameId}                    ✅
live updates                             ✅
live diff                                ✅
live semantic events                     ✅
match-end                                ✅
cleanup                                  ✅

prematch/header                          ✅
prematch/games                           ✅
prematch/markets                         ✅

prematch/games UpdateList                ✅
duplicate suppression                    ✅
DeleteList observed                      ✅ but semantics unknown

getprematchgameall                       ✅
getprematchgamefull                      ✅
gameall batch support                    ✅
team mapping                             ✅

gameall partial                          ✅ PROVEN
gamefull authoritative                   ✅ PROVEN
market-level replacement                 ✅ useful approximation

pc mismatch resync                       ✅
real prematch odds changes               ✅
real selection additions                 ✅

pc match = exact state                   ❌ DISPROVEN
missing market = unchanged               ❌ DISPROVEN
gameall complete logical delta            ❌ DISPROVEN

stable authoritative mismatch            ✅
periodic reconciliation repair           ✅

prematch/markets actual odds payload      ❌
prematch/markets timestamp marker         ✅ strong evidence

clean browser update trigger              ⏳ NEXT
prematch → live transition                ⏳ partly observed
DeleteList semantics                      ⏳
pc formal semantics                       ⏳
mc formal semantics                       ⏳
persistent fixture registry               ⏳ later
storage                                    ⏳ later
```

---

# 69. EXACT NEXT SESSION STARTING PROMPT

Yeni chat açıldığında şunu yaz:

```text
Devam edelim.

Project: mystake-collector.

Şu ana kadar:
- live MQTT tarafı çalışıyor.
- prematch/header, prematch/games, prematch/markets topic’lerini bulduk.
- gameall partial ve complete logical delta değil.
- gamefull authoritative.
- pc match exact correctness garanti etmiyor.
- reconciliation ile bunu kanıtladık.
- prematch/markets cache’i gerçek market data değil, 10 dakikalık timestamp/version marker gibi davranıyor.
- kickoff civarında prematch/games içinde GameId geldiğinde gameall/gamefull state’in 0 market / 0 selection / visible=false olduğu örnek gördük.
- son testler kickoff’a çok yakın olduğu için browser tarafındaki gamefull çağrıları temiz prematch update davranışı olarak kullanılamadı.

Şimdi hedef:
15–30 dakika sonra başlayacak temiz bir prematch maç seçip
prematch/games UpdateList içindeki GameId ile
browser Fetch/XHR request’lerini HAR timestamp üzerinden korele etmek.

Önce yeni match URL/GameId seçeceğiz.
Sonra DevTools recording + HAR export yapacağız.
Daha sonra gerekirse HAR parser yazacağız.

Kod tarafında henüz production refactor yapmıyoruz.
Protocol correctness öncelikli.

Bana tek adım ver, ben terminal/DevTools çıktısını göndereyim.
```

---

# 70. FINAL REMINDER

Şu anda en büyük hata şunu yapmak olur:

```text
gameall merge logic'i biraz daha tweak ederek
problemi çözdüğümüzü sanmak
```

Çünkü artık kanıtlandı:

```text
gameall bazı authoritative changes'i hiç taşımıyor
```

Gerçek frontend davranışını anlamamız gerekiyor.

Bir sonraki hedef:

```text
CLEAN PREMATCH TEST
→ UpdateList event
→ HAR
→ exact browser request correlation
```

Bunu çözmeden production architecture’a geçme.