# MyStake Collector — Handoff / Continuation Context Map

Bu doküman `mystake-collector` projesine yeni bir ChatGPT session'ında kaldığımız yerden eksiksiz devam edebilmek için hazırlanmıştır.

Bu dokümandaki bilgiler:

- gerçek MyStake trafiği,
- çalışan Python kodu,
- Chrome DevTools gözlemleri,
- gerçek MQTT paketleri,
- soak testleri,
- `getprematchgameall`,
- `getprematchgamefull`,
- live MQTT,
- prematch MQTT,
- reconciliation testleri

üzerinden doğrulanmıştır.

Tahmin ile protocol semantics üretme.

Ana çalışma prensibi:

```text
observe
→ experiment
→ prove
→ only then productionize
```

Gereksiz refactor yapma.

Kullanıcı PyCharm ve macOS terminal kullanıyor.

Her development adımında mümkünse:

```text
exact filename
complete code/file
exact command
expected output
```

ver.

Kullanıcı terminal çıktısını gönderdikten sonra bir sonraki development objective'e geç.

---

# 1. PROJECT GOAL

Project:

```text
mystake-collector
```

Amaç:

MyStake'tan browser bağımsız şekilde:

```text
fixture discovery
prematch fixture state
prematch markets
prematch selections
prematch odds
live markets
live odds
live score/state
match lifecycle
match end
cleanup
```

toplamak.

Sadece canlı maçları takip etmiyoruz.

Hedef full lifecycle:

```text
fixture appears
→ PREMATCH
→ prematch markets / selections / odds updates
→ kickoff
→ LIVE
→ live score / state / markets / odds
→ MATCH_ENDED
→ final state
→ cleanup
```

Uzun vadeli sistem:

```text
Fixture discovery
+
Prematch state
+
Live state
+
Lifecycle transition
+
Cleanup
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

Ana komutlar:

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

Son regression baseline:

```text
47 passed
```

Bu baseline korunmalı.

---

# 3. CURRENT PROJECT STRUCTURE

Mevcut PyCharm structure:

```text
mystake-collector/
├── .venv/
│
├── docs/
│   ├── handoff/
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

# 4. ARCHITECTURE PRINCIPLE

Katmanlar:

```text
sources/
    raw transport / protocol

pipeline/
    decode / transform / diff

events/
    semantic domain events

future:
    models/
    lifecycle/
    storage/
```

ÖNEMLİ:

`mystake/sources/mqtt/client.py` transport/protocol seviyesinde kalmalı.

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

Endpoint:

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

Client ID browser davranışına benzer şekilde millisecond timestamp kullanıyor.

Çalışan sequence:

```text
CONNECT
→ CONNACK
→ SUBSCRIBE
→ SUBACK
→ PUBLISH
→ PINGREQ / PINGRESP
```

Reconnect de gerçek broker üzerinde doğrulandı.

---

# 6. MQTT RECONNECT — PROVEN

Gerçek testte:

```text
MQTT connection lost
→ WebSocket closed
→ reconnect
→ CONNACK
→ restore subscriptions
→ subscribe prematch/games
→ SUBACK
→ continue receiving
```

çalıştı.

Son reconciliation soak testinde remote peer bağlantıyı kapattı ve client otomatik reconnect/resubscribe yaparak çalışmaya devam etti.

Dolayısıyla reconnect + subscription restore şu anda çalışan davranış.

---

# 7. MQTT UNSUBSCRIBE — PROVEN

Gerçek broker üzerinde:

```text
UNSUBSCRIBE
→ UNSUBACK
```

doğrulandı.

Önemli implementation detayı:

Broker bazen `UNSUBACK` gelmeden önce başka `PUBLISH` frame'leri gönderebilir.

Possible sequence:

```text
PUBLISH
PUBLISH
UNSUBACK
```

Bu nedenle MQTT client içindeki pending packet queue korunmalı.

---

# 8. MQTT LIVE TOPIC — PROVEN

Exact live topic:

```text
live/gamenew/{GameId}
```

Örnek:

```text
live/gamenew/74679515
```

Exact subscription çalışıyor.

Wildcard:

```text
live/gamenew/#
```

test edildi.

Broker sonucu:

```text
SUBACK 0x80
```

Yani wildcard reddediliyor.

Dolayısıyla live discovery wildcard MQTT üzerinden yapılamıyor.

---

# 9. MQTT CACHE NOTIFICATION MODEL

MQTT `PUBLISH` çoğu zaman doğrudan game payload taşımıyor.

Payload örneği:

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

Bu pipeline implement edildi ve çalışıyor.

---

# 10. NotificationProcessor DETAIL

`NotificationProcessor.process(message)` doğrudan `dict` dönmüyor.

Return object:

```text
ProcessedNotification
```

Decoded payload:

```python
processed.data
```

üzerinden alınmalı.

Bu daha önce düzeltilmiş önemli bir detay.

---

# 11. LIVE SNAPSHOT — PROVEN

Live snapshot içinde gözlenen ana alanlar:

```text
gmk
TimeLines
Match
mk
```

Live diff logic implement edildi.

Semantic live events üretilebiliyor.

---

# 12. LIVE SEMANTICS

Şu davranışlar gerçek trafik üzerinde gözlendi.

## visible=false

```text
visible=false
```

tek başına bütün marketin suspended olduğu anlamına gelmiyor.

## LiveBetStatus=false

```text
LiveBetStatus=false
```

tek başına maç bitti anlamına gelmiyor.

Geçici suspension olabilir.

## Football match end

Güçlü gözlenen pattern:

```text
Status == 3
BetStatus == 0
EventStatus == 40
```

`LiveBetStatus=false` eşlik edebilir fakat tek başına match-end kriteri değildir.

---

# 13. REAL LIVE MATCH-END TEST

Gerçek GameId:

```text
75297868
```

Maç yaklaşık 88. dakikadan bitişe kadar izlendi.

Doğrulanan chain:

```text
subscribe
→ live updates
→ match-end detection
→ final state
→ cleanup
```

---

# 14. PREMATCH GLOBAL MQTT TOPIC — PROVEN

Prematch notification topic:

```text
prematch/games
```

Exact subscription çalışıyor.

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

---

# 15. prematch/games FRAME — CHROME DEVTOOLS ALSO CONFIRMED

Chrome DevTools WebSocket frame içinde görülen binary/hex payload:

```text
30 ...
00 0E
prematch/games
cache:https://...
```

Decoded meaning:

```text
MQTT PUBLISH
topic = prematch/games
payload = cache:https://...
```

Yani browser trafiği de Python collector modelimizi doğruladı:

```text
prematch/games
→ cache URL
→ HTTP fetch
→ UpdateList / DeleteList
```

---

# 16. PREMATCH/GAMES SEMANTICS

`UpdateList` full fixture universe değildir.

Daha çok:

```text
change notification
/
update signal
```

gibi davranıyor.

Aynı payload tekrar gelebiliyor.

Exact duplicate suppression implement edildi.

Örnek:

```text
Duplicate : True
Skipping exact duplicate notification.
```

Duplicate suppression değerli ve korunmalı.

---

# 17. UpdateTimeStamp ODDITY

`UpdateTimeStamp` bazen:

```text
13 digit millisecond-like
```

bazen:

```text
10 digit second-like
```

geliyor.

Exact semantics henüz kesin değil.

Monotonic sequence/version gibi güvenilmemeli.

Normalize edilmeden önce daha fazla kanıt gerekiyor.

---

# 18. PREMATCH FULL ENDPOINT — PROVEN

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

Tahminle isim verme.

---

# 19. GETPREMATCHGAMEFULL FORMAT

Outer response:

```json
{
  "game": "...JSON string...",
  "price": "[]",
  "disableMarkets": null
}
```

`game` JSON encoded string.

Decode:

```python
outer = response.json()
game = json.loads(outer["game"])
```

`gamefull` authoritative/full snapshot olarak davranıyor.

---

# 20. PREMATCH GAME STRUCTURE

Gözlenen fields:

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

Kısa field'lara kanıt olmadan semantic isim uydurma.

---

# 21. PREMATCH MARKET STRUCTURE

`game.ev`:

```text
ev
└── marketId
    └── selectionId
        ├── pos
        ├── coef
        ├── res
        ├── lock
        ├── h
        ├── hism
        ├── p1
        └── pl
```

Örnek:

```json
"448": {
  "11534047616": {
    "pos": 1,
    "coef": 1.33,
    "res": 0,
    "lock": false
  }
}
```

Player markets `p1` / `pl` gibi ek alanlar taşıyabiliyor.

---

# 22. GETPREMATCHGAMEALL — PROVEN

Endpoint:

```text
https://analytics-sp.googleserv.tech/api/prematch/getprematchgameall/fr/28/?games=,75433979
```

Pattern:

```text
/api/prematch/getprematchgameall/{LANGUAGE}/{CONTEXT_ID}/?games=,{GAME_IDS}
```

Şimdilik:

```python
PREMATCH_LANGUAGE = "fr"
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

# 23. GAMEALL BATCH SUPPORT — PROVEN

Bir HTTP request içinde birden fazla GameId gönderilebiliyor.

Örnek:

```text
?games=,75433979,75747467
```

Gözlenen:

```text
requested games: 2
returned games : 2
teams          : 4
```

Bu özellik korunmalı.

---

# 24. TEAM ID → NAME — PROVEN

Mapping:

```text
game.t1 / game.t2
→ teams[].ID
→ teams[].Name
```

Örnek:

```text
10460 -> Flamengo RJ
10473 -> Red Bull Bragantino SP
```

çalışıyor.

---

# 25. MULTI-SPORT PREMATCH STREAM — PROVEN

`prematch/games` birden fazla sport taşıyor.

Observed IDs arasında:

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
sport=1 → football-looking
sport=3 → baseball example
sport=5 → tennis examples
```

Ama bilinmeyen sport ID'lere tahminle isim verme.

---

# 26. FIRST MAJOR DISCOVERY — GAMEALL IS PARTIAL

Aynı GameId için `gameall` ve `gamefull` karşılaştırıldı.

Önemli fixture:

```text
75747467
Flamengo RJ vs Red Bull Bragantino SP
```

Observed:

```text
getprematchgameall
bytes       ≈ 9 KB
mc          = 384
pc          = 6793
ev markets  = 6
ev selects  = 81
```

vs:

```text
getprematchgamefull
bytes       ≈ 782 KB
mc          = 384
pc          = 6793
ev markets  = 234
ev selects  = 6793
```

Comparison:

```text
gameall markets subset of gamefull = True
gameall selections subset of gamefull = True

markets only in gameall = 0
markets only in gamefull = 228

selections only in gameall = 0
selections only in gamefull = 6712
```

İlk proven model:

```text
getprematchgameall
=
PARTIAL payload
```

```text
getprematchgamefull
=
FULL snapshot
```

---

# 27. ORIGINAL IMPORTANT RULE

Şu zaten kanıtlandı:

```text
market missing from gameall
≠
market removed
```

Yani `gameall` full snapshot gibi diff edilmemeli.

---

# 28. MARKET-LEVEL REPLACEMENT DISCOVERY

İlk merge implementation selection-by-selection upsert idi.

Yani:

```text
market exists
→ old selections preserve
→ new selections upsert
```

Gerçek trafikte bunun hatalı olduğu görüldü.

Bir market `gameall.ev` içinde GELİYORSA, o market'in selection dictionary'si current market set gibi davranıyor.

Bu yüzden merge değiştirildi:

```text
market absent from delta
→ preserve local market

market present in delta
→ replace entire local market
```

Implementation mantığı:

```python
for market_id, delta_selections in delta["ev"].items():
    merged["ev"][market_id] = deepcopy(delta_selections)
```

Bu selection-level upsert'ten daha doğru çıktı.

---

# 29. TOP-LEVEL SCALAR MERGE

Delta içinde gelen scalar fields:

```text
up
pc
mc
st
...
```

local state üzerine replace ediliyor.

`ev` ayrıca market-level işleniyor.

---

# 30. pc CONSISTENCY DISCOVERY

`pc` ile full selection count arasında çok güçlü ilişki var.

Örnek:

```text
gamefull pc = 6793
count(full ev selections) = 6793
```

Bunun üzerine integrity check eklendi.

Merge sonrası:

```text
local selection count
vs
server pc
```

Rule:

```text
local_count == pc
→ provisionally accept

local_count != pc
→ state suspect
→ gamefull resync
```

Bu fallback halen değerli.

---

# 31. COUNT-MISMATCH RESYNC — PROVEN

Gerçek örneklerde:

```text
local=402
server_pc=407
```

gibi mismatch görüldü.

`gamefull` resync sonrası:

```text
407
```

geldi.

Başka örnek:

```text
GameId=76457372
resynced selections=0
pc=0
```

Başka:

```text
GameId=76456861
resynced selections=88
pc=88
```

Yani `pc` mismatch safety check gerçekten corrupt/incomplete state'i yakalıyor.

---

# 32. REAL PREMATCH ODDS CHANGES — PROVEN

Merge pipeline gerçek odds değişikliklerini yakaladı.

Örnek:

```text
coef: 1.82 -> 1.90
coef: 1.92 -> 1.84
```

Başka:

```text
2.17 -> 2.12
1.59 -> 1.62
```

Proven chain:

```text
prematch/games
→ getprematchgameall
→ partial payload
→ market-level replacement merge
→ full local state
→ diff
→ real price change
```

---

# 33. REAL SELECTION ADDITIONS — PROVEN

Örnek:

```text
pc: 16 -> 18
```

ve:

```text
market=2117

added selections:
11606876691
11606876692
```

Merge sonrası:

```text
local=18
server_pc=18
```

görüldü.

---

# 34. DELETE LIST

`DeleteList` bugüne kadar çoğunlukla:

```text
[]
```

geldi.

Exact semantics halen bilinmiyor.

DeleteList gelirse şimdilik:

```text
observe
log
do NOT delete state based on assumption
```

---

# 35. LOCK CHANGES

Soak testlerde şimdiye kadar:

```text
Lock changes = 0
```

gözlendi.

Bu:

```text
lock never changes
```

anlamına gelmez.

Sadece observation window içinde görülmedi.

---

# 36. FIRST 5-MINUTE MERGE SOAK TEST

Önemli checkpoint:

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

Bu ilk etapta market-level replacement modelini güçlü şekilde destekledi.

Fakat kritik soru şuydu:

```text
count match
==
exact authoritative state ?
```

Bunu bilmiyorduk.

---

# 37. SAMPLED FULL-STATE VALIDATION

Bu yüzden `inspect_prematch_games.py` içine sampled validation eklendi.

İlk tasarım:

```python
VALIDATE_EVERY_COUNT_MATCHES = 20
```

Flow:

```text
successful count-match merge
→ every Nth sample
→ getprematchgamefull
→ compare local vs full
```

Başlangıçta:

```text
merged.up == full.up
```

ise race olmadığı varsayılıp exact comparison yapıldı.

İlk sample:

```text
RESULT: EXACT MATCH
```

geldi.

Bu umut vericiydi fakat yeterli değildi.

---

# 38. CRITICAL DISCOVERY — SAME-COUNT HIDDEN DIVERGENCE

Daha sonra gerçek:

```text
RESULT: EXACT MISMATCH
```

geldi.

Önemli örnek:

```text
GameId=76295715
Madla vs Haugesund 2
```

Observed:

```text
merged up        = same
full up          = same
local pc         = 224
full pc          = 224
local markets    = 49
full markets     = 49
local selections = 224
full selections  = 224
```

Buna rağmen çok sayıda selection için:

```text
local coef != full coef
```

görüldü.

Yani:

```text
pc match
market count match
selection count match
up match
```

olmasına rağmen state yanlış/stale olabiliyor.

---

# 39. SECOND SAME-COUNT MISMATCH

Başka GameId:

```text
76436060
Sychra, Martin vs Kasnik, Sebastian
```

Observed:

```text
local pc         = 30
full pc          = 30
local markets    = 7
full markets     = 7
local selections = 30
full selections  = 30
merged up        = full up
```

ama yine birden fazla selection `coef` değeri farklı çıktı.

Dolayısıyla bu tekil anomaly değildi.

---

# 40. VALIDATOR ENHANCEMENT

Bunun race olup olmadığını anlamak için validator geliştirildi.

`EXACT MISMATCH` olduğunda:

```text
1. mismatch market IDs
2. market present_in_current_delta ?
3. immediate second gamefull fetch
4. compare full #1 vs full #2
```

Classification:

```text
LOCAL != FULL1
FULL1 == FULL2
→ STABLE_MISMATCH
```

```text
FULL1 != FULL2
→ HIDDEN_RACE / STATE_ADVANCED_WITH_SAME_UP
```

---

# 41. CRITICAL PROOF — STABLE_MISMATCH

Gerçek fixture:

```text
GameId=76447616
Aurora Zantedeschi vs Lombardini, Marta
```

Observed:

```text
merged up : 1789975620
full #1 up: 1789975620
full #2 up: 1789975620

local == full #2 : False
full #1 == full #2: True
```

Classification:

```text
STABLE_MISMATCH
```

Bu race değildi.

Authoritative full state iki ardışık request boyunca stabildi.

---

# 42. MOST IMPORTANT PREMATCH DISCOVERY

Stable mismatch içindeki mismatch marketlerin tamamında:

```text
present_in_current_delta=False
```

görüldü.

Fakat authoritative `gamefull` içinde bu marketlerin odds değerleri değişmişti.

Bu nedenle önceki varsayım:

```text
market absent in delta
→ market unchanged
```

YANLIŞ.

Artık proven:

```text
market absent in gameall
≠ removed
```

VE:

```text
market absent in gameall
≠ unchanged
```

Bu son derece önemli.

---

# 43. EVEN STRONGER EXAMPLE — SELECTION IDENTITY CHANGED

Stable mismatch'te bir markette:

```text
market=580
```

gözlendi.

Local:

```text
11609165868
11609165869
```

Full:

```text
11609158205
11609158206
```

Yani:

```text
same market ID
same selection count
different selection IDs
```

ve market current delta içinde hiç yoktu.

Bu şu sonucu daha da güçlendirdi:

```text
getprematchgameall
is NOT a complete logical delta.
```

---

# 44. CURRENT UNDERSTANDING OF GAMEALL

Artık en doğru evidence-based model:

```text
getprematchgameall
=
partial notification/update payload
```

Ama:

```text
NOT authoritative full snapshot
NOT guaranteed complete logical delta
```

Dolayısıyla yalnızca `gameall` merge ederek exact full prematch state'i sonsuza kadar korumak mümkün görünmüyor.

---

# 45. GAMEFULL ROLE

Current strongest model:

```text
gamefull
=
authoritative snapshot
```

`gamefull`:

- bootstrap için,
- mismatch recovery için,
- reconciliation için

kullanılmalı.

Ancak bazı fixtures için payload:

```text
hundreds of KB
```

ve örnek olarak:

```text
~782 KB
```

olabildiği için her update'te çağırmak pahalı olabilir.

---

# 46. PERIODIC RECONCILIATION EXPERIMENT

Sample validator'dan sonra bir sonraki deney:

```text
per-GameId periodic authoritative reconciliation
```

olarak yapıldı.

İlk threshold:

```python
RECONCILE_EVERY_GAME_MERGES = 10
```

Flow:

```text
FIRST SEEN
→ gamefull bootstrap

KNOWN GAME
→ gameall merge

per same GameId:
10 successful merges
→ gamefull reconciliation

if local == full
→ no repair

if authoritative difference
→ second full snapshot diagnostic

if full1 == full2
→ stable authoritative difference
→ replace local state with authoritative full

if full1 != full2
→ reconciliation race
```

---

# 47. REAL RECONCILIATION REPAIR — PROVEN

Fixture:

```text
GameId=76448598
Fernandez, Bruno vs Farjat, Tomas
```

Reconciliation result:

```text
RESULT: AUTHORITATIVE DIFFERENCE
```

Mismatch markets yine:

```text
present_in_current_delta=False
```

idi.

Immediate second FULL:

```text
merged up : 1789976211
full #1 up: 1789976211
full #2 up: 1789976211

local == full #2: False
full #1 == full #2: True
```

Classification:

```text
STABLE_AUTHORITATIVE_DIFFERENCE
```

Repair:

```text
RECONCILIATION REPAIR

Reason:
AUTHORITATIVE_FULL_DIFFERENCE

Action:
local state replaced with newest successful gamefull snapshot
```

State sizes:

```text
Markets   : 18 -> 18
Selections: 54 -> 54
```

Ama çok sayıda `coef` düzeltildi.

Bu reconciliation mekanizmasının gerçekten state repair ettiğini kanıtladı.

---

# 48. 10-MINUTE RECONCILIATION SOAK TEST

Son ve en önemli checkpoint:

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

Critical reconciliation rate:

```text
checks  = 11
matches = 1
repairs = 10
```

Repair rate:

```text
10 / 11
≈ 90.9%
```

Exact match rate:

```text
1 / 11
≈ 9.1%
```

Bu çok yüksek repair oranı.

---

# 49. CRITICAL INTERPRETATION OF SOAK TEST

`pc` count integrity:

```text
950 / 983
≈ 96.64%
```

çok iyi görünüyor.

AMA reconciliation:

```text
10 / 11
```

kez authoritative difference yakaladı.

Dolayısıyla:

```text
pc count-match
```

artık sadece structural sanity check olarak kullanılabilir.

Şu an kesin olarak:

```text
pc match
≠ exact state correctness
```

kanıtlandı.

---

# 50. IMPORTANT CONCLUSION — DO NOT JUST LOWER RECONCILIATION TO 5 OR 1

Repair rate `%90.9` olduğu için:

```text
every 10 merges
→ gamefull
```

bile local state'in sık stale kaldığını gösteriyor.

Threshold'u:

```text
10 → 5 → 1
```

diye düşürmek problemi brute-force `gamefull` polling ile çözmek olur.

`gamefull` büyük olduğu için bu production architecture açısından iyi olmayabilir.

Bu nedenle şimdilik reconciliation interval tuning yapma.

---

# 51. CURRENT MOST IMPORTANT HYPOTHESIS

Muhtemelen MyStake frontend'in prematch realtime odds için bizim henüz bulmadığımız başka bir mekanizması var.

Possible examples:

```text
GameId-specific MQTT topic
prematch price topic
market-specific topic
another WebSocket feed
another cache key
another incremental endpoint
```

Ama bunlar SADECE arama hipotezleri.

Exact topic isimlerini tahmin edip doğru kabul etme.

---

# 52. NEW PRIMARY OBJECTIVE

Şu anda bir sonraki ana hedef:

```text
FIND THE REAL PREMATCH REALTIME PRICE UPDATE CHANNEL
```

Yani:

```text
MyStake browser odds değişikliklerini gerçekten nereden alıyor?
```

sorusunu çözmek.

Şimdilik Python merge algoritmasını değiştirmiyoruz.

Yeni hedef protocol discovery.

---

# 53. CHROME DEVTOOLS TEST — CURRENT STATE

Bir prematch maç detail page açıldı.

Örnek görülen fixture:

```text
Pays-Bas
vs
Allemagne

Jeudi, 24 Septembre 21:45
```

Chrome DevTools:

```text
Network
→ Socket
```

açıldı.

İlk problem:

Gelecekteki bir maç olduğu için oran değişmesini beklemek verimsiz olabilir.

Ancak artık biliyoruz ki oran değişikliğini beklemek şart değil.

Önce browser'ın hangi MQTT topic'lerine SUBSCRIBE olduğunu bulabiliriz.

---

# 54. CHROME MQTT FRAME FOUND

DevTools'ta görülen frame UTF-8/hex içeriğinde:

```text
prematch/games
cache:https://...
```

bulundu.

Bu frame MQTT PUBLISH.

Header başlangıcı yaklaşık:

```text
30
```

ve topic length:

```text
00 0E
```

sonrasında:

```text
prematch/games
```

geliyor.

Bu Python implementation'ı browser tarafında tekrar doğruladı.

---

# 55. NEXT EXACT CHROME TASK

Şu an aradığımız şey SERVER → CLIENT PUBLISH değil.

Aradığımız:

```text
CLIENT → SERVER MQTT SUBSCRIBE
```

MQTT SUBSCRIBE packet fixed header çoğunlukla:

```text
82
```

ile başlar.

Ama packet fragmentation / viewer formatting nedeniyle sadece bu byte'a körü körüne güvenme.

DevTools workflow:

```text
1. Network açık
2. Keep log açık
3. Recording açık
4. Socket filter
5. Page refresh
6. MQTT WebSocket connection seç
7. Messages / Frames aç
```

Sonra:

```text
prematch match detail sayfasından çık
→ birkaç saniye bekle
→ aynı veya başka prematch match detail aç
```

Yeni CLIENT → SERVER binary frameleri gözle.

Özellikle yeni MQTT SUBSCRIBE gelip gelmediğini kontrol et.

---

# 56. WHAT WE ARE LOOKING FOR

Şu tip bir şey olup olmadığını bulmak istiyoruz:

```text
prematch/<something>/<GameId>
```

Örneğin olası isimler:

```text
prematch/game/{GameId}
prematch/gamenew/{GameId}
prematch/price/{GameId}
prematch/market/{GameId}
```

BUNLAR SADECE ÖRNEK HİPOTEZLERDİR.

Gerçek topic bulunmadan hiçbirini protocol rule olarak kabul etme.

---

# 57. VERY IMPORTANT NEGATIVE RESULT

Eğer prematch match detail açılırken:

```text
no new MQTT SUBSCRIBE
```

gelirse bu da çok önemli bir sonuç.

Bu durumda frontend muhtemelen:

```text
global prematch/games
+
HTTP API calls
```

ile çalışıyor olabilir.

O zaman XHR/Fetch traffic tarafına yoğunlaşacağız.

---

# 58. IF NO GAME-SPECIFIC MQTT EXISTS

Sonraki araştırma alanları:

```text
Fetch/XHR
WebSocket
cache keys
API calls
JavaScript bundle
network initiator
```

Özellikle odds değişiminde browser'ın hangi endpoint'i tekrar çağırdığı incelenecek.

Filtre kelimeleri:

```text
prematch
game
price
coef
market
cache
mqtt
```

---

# 59. POSSIBLE BETTER TEST FIXTURE

24 Eylül gibi uzak maç yerine:

```text
today
starting soon
5–30 minutes before kickoff
popular football
tennis
basketball
```

gibi daha aktif prematch fixture tercih edilebilir.

Çünkü odds update frequency daha yüksek olabilir.

Fakat SUBSCRIBE discovery için oran değişmesini beklemek zorunlu değildir.

---

# 60. CURRENT inspect_prematch_games.py ROLE

Root file:

```text
inspect_prematch_games.py
```

Bu production implementation değildir.

Şu anda:

```text
reverse engineering
protocol validation
state-model experimentation
reconciliation experimentation
```

için kullanılıyor.

Bu file şu işlerin çoğunu yapıyor:

```text
subscribe prematch/games
decode notification
duplicate suppression
batch gameall
team map
full bootstrap
market-level merge
pc integrity check
full resync
diff
sample diagnostics
periodic reconciliation
authoritative repair
stats
```

Production module'a henüz bölme.

---

# 61. CURRENT MERGE MODEL — KEEP FOR EXPERIMENTS

Current local experimental merge:

```text
delta scalar present
→ replace local scalar

market present in gameall
→ replace entire local market

market absent in gameall
→ preserve local market TEMPORARILY
```

Son satır artık authoritative correctness rule değildir.

Sadece local incremental approximation'dır.

Çünkü kanıtlandı:

```text
market absent
can still have changed authoritative data
```

---

# 62. CURRENT STATE SAFETY MODEL

Halen:

```text
local selection count != pc
→ immediate gamefull resync
```

mantıklı safety net.

Ama:

```text
local selection count == pc
```

exact correctness guarantee etmez.

Bu ayrımı unutma.

---

# 63. CURRENT STRONGEST PREMATCH MODEL

Şu anda evidence-based model:

```text
prematch/games
        ↓
change / fixture notification
        ↓
getprematchgameall
        ↓
partial update/view
        ↓
local approximate state
```

Authoritative:

```text
getprematchgamefull
        ↓
authoritative full snapshot
```

Ancak gerçek realtime odds mechanism henüz tam bulunmadı.

---

# 64. IMPORTANT OPEN QUESTIONS

Priority order:

```text
1. Browser prematch odds update'larını gerçekte nereden alıyor?

2. Match detail açarken GameId-specific MQTT SUBSCRIBE var mı?

3. prematch/games dışında başka MQTT prematch topic var mı?

4. Odds değişirken başka cache topic/key geliyor mu?

5. Browser getprematchgamefull'i ne zaman çağırıyor?

6. Browser getprematchgameall'i ne zaman çağırıyor?

7. Başka incremental price endpoint var mı?

8. gameall'ın gerçek frontend purpose'u nedir?

9. DeleteList semantics nedir?

10. pc exact semantics nedir?

11. mc exact semantics nedir?

12. PREMATCH GameId == LIVE GameId mi?

13. kickoff transition nasıl keşfediliyor?

14. prematch fixture ne zaman live subscription'a geçiyor?

15. fixture removal lifecycle nasıl çalışıyor?
```

---

# 65. PREMATCH → LIVE FUTURE TEST

İleride önemli test:

Bir prematch fixture kickoff olduğunda aynı:

```text
GameId
```

şurada kullanılabiliyor mu?

```text
live/gamenew/{GameId}
```

Ana soru:

```text
PREMATCH GameId == LIVE GameId ?
```

Bunu gerçek fixture ile kanıtla.

Tahminle true kabul etme.

---

# 66. EVENTUAL DESIRED FLOW

Uzun vadeli hedef halen:

```text
fixture discovery
        ↓
prematch state
        ↓
real prematch odds updates
        ↓
kickoff transition
        ↓
live/gamenew/{GameId}
        ↓
live state
        ↓
MATCH_ENDED
        ↓
cleanup
```

---

# 67. FUTURE PRODUCTION MODULES

Protocol yeterince çözüldükten sonra muhtemel structure:

```text
mystake/
├── sources/
│   ├── mqtt/
│   ├── cache/
│   └── prematch/
│       └── client.py
│
├── pipeline/
│   ├── prematch_decoder.py
│   ├── prematch_merger.py
│   └── prematch_diff.py
│
├── models/
│   └── prematch.py
│
└── lifecycle/
    └── fixture_registry.py
```

Ama HENÜZ bunu yapma.

Önce real prematch realtime update mechanism'i bul.

---

# 68. DO NOT BUILD YET

Henüz:

```text
PostgreSQL
Redis
Kafka
RabbitMQ
production DB schema
persistent fixture registry
Cloud deployment
Java rewrite
large refactor
```

yapma.

Protocol correctness öncelikli.

---

# 69. CURRENT MILESTONE STATUS

```text
MQTT WebSocket connection                 ✅
MQTT CONNECT / CONNACK                    ✅
SUBSCRIBE / SUBACK                        ✅
PUBLISH                                   ✅
keepalive                                 ✅
reconnect                                 ✅
resubscribe                               ✅
unsubscribe                               ✅

cache URL notification                    ✅
HTTP cache fetch                          ✅
base64/gzip/json decode                   ✅

live exact GameId topic                   ✅
live state decode                         ✅
live diff                                 ✅
semantic live events                      ✅
live match-end                            ✅
live cleanup                              ✅

prematch/games                            ✅
UpdateList                                ✅
duplicate suppression                     ✅
GameId discovery                          ✅

getprematchgameall                        ✅
batch gameall                             ✅
teams metadata                            ✅
multi-sport                               ✅

getprematchgamefull                       ✅
full snapshot                             ✅

gameall is partial                        ✅ PROVEN
gamefull is full                          ✅ PROVEN

market-level replacement                  ✅ USEFUL
real prematch price changes               ✅ PROVEN
selection additions                       ✅ PROVEN
pc integrity check                        ✅
count mismatch resync                     ✅

pc match == exact state                   ❌ DISPROVEN

same-count hidden divergence              ✅ PROVEN

missing market == unchanged               ❌ DISPROVEN

stable authoritative mismatch             ✅ PROVEN

periodic reconciliation repair            ✅ PROVEN

10-merge reconciliation sufficient        ❌ NO
repair rate at 10 merges                   ≈ 90.9%

real prematch incremental price channel    ⏳ NEXT
prematch-specific extra MQTT topics        ⏳ NEXT
DeleteList semantics                       ⏳
pc formal semantics                        ⏳
mc formal semantics                        ⏳
prematch→live transition                   ⏳
persistent fixture registry                ⏳ LATER
storage                                    ⏳ LATER
```

---

# 70. LATEST IMPORTANT SOAK CHECKPOINT

Keep this exact latest result:

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
checks  = 11
matches = 1
repairs = 10
races   = 0
```

Repair rate:

```text
≈ 90.9%
```

---

# 71. MOST IMPORTANT CONCLUSION RIGHT NOW

Do NOT spend the next session tuning:

```text
RECONCILE_EVERY_GAME_MERGES
```

yet.

Do NOT simply change:

```text
10 → 5
```

or:

```text
10 → 1
```

The next task is not reconciliation tuning.

The next task is:

```text
FIND THE REAL PREMATCH REALTIME UPDATE MECHANISM USED BY THE BROWSER
```

---

# 72. EXACT NEXT SESSION STARTING POINT

Yeni chat açıldığında şu instruction ile devam et:

```text
Devam edelim.

Şu anda Python kodunu değiştirmiyoruz.

Son kanıtımız:
- gameall authoritative logical delta değil.
- pc match exact correctness garanti etmiyor.
- 10-merge reconciliation check'lerinin 10/11'i repair gerektirdi.
- browser'da prematch/games MQTT PUBLISH frame'ini de doğruladık.

Şimdi hedefimiz Chrome DevTools kullanarak MyStake frontend'in gerçek prematch realtime odds update mekanizmasını bulmak.

Bir prematch match detail açtım.

Önce MQTT WebSocket Messages/Frames içinde CLIENT → SERVER SUBSCRIBE framelerini inceleyelim.

Özellikle maç detail açılırken prematch/games dışında yeni GameId-specific veya price/market-specific MQTT topic subscribe ediliyor mu bunu kanıtlayalım.

Bana her seferinde tek bir DevTools adımı söyle. Ben ekran görüntüsü / frame / hex / UTF-8 çıktısını göndereceğim.
```

---

# 73. USER WORKING STYLE

Kullanıcı:

```text
macOS
PyCharm
Chrome DevTools
terminal
```

kullanıyor.

Tercih:

```text
Türkçe
kısa ama kesin
tek adım
sonucu gönder
sonra sonraki adım
```

Kod değişikliklerinde mümkünse:

```text
complete file
```

istiyor.

Protocol reverse engineering'de:

```text
önce gözlem
sonra yorum
```

şeklinde ilerle.

---

# 74. FINAL REMINDER

Şu ana kadarki en önemli discovery:

```text
gameall partial olması tek problem değil.

gameall bazı authoritative changes'i
current delta içinde hiç taşımayabiliyor.
```

Bunun sonucu:

```text
missing market
≠ removed

missing market
≠ unchanged
```

ve:

```text
pc match
≠ exact state correctness
```

Bu artık gerçek trafik ve stable double-gamefull validation ile kanıtlandı.

Bir sonraki adım:

```text
Chrome DevTools
→ MQTT client SUBSCRIBE frames
→ match-specific prematch topic var mı?
```

Bunu çözmeden yeni production architecture tasarlama.