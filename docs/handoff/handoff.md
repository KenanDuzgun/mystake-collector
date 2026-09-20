# MyStake Collector — Handoff / Continuation Context Map

Bu doküman `mystake-collector` projesinin yeni chat session’ında kaldığı yerden devam edebilmesi için hazırlanmıştır.

Bu dokümandaki bilgiler gerçek trafik üzerinde yapılan testler, çalışan kod, terminal çıktıları ve bugüne kadar doğrulanmış davranışlara dayanır.

Lütfen bu bağlamı dikkatlice oku ve mevcut kanıtlanmış davranışları bozma.

Özellikle:
- Tahminle protocol semantics uydurma.
- Erken refactor yapma.
- Önce gözlem, sonra deney, sonra kanıt, sonra production yapısına taşı.
- Bir sonraki adımlarda da exact filename, complete code, exact command ve expected output ver.
- Kullanıcının terminal çıktısını gördükten sonra ancak bir sonraki adıma geç.

---

# 1. PROJECT GOAL

Proje:

```text
mystake-collector
```

Amaç:

MyStake’tan browser bağımsız şekilde fixture, prematch odds, live odds ve maç lifecycle toplamak.

Sadece canlı maçlar hedeflenmiyor.

Tam lifecycle:

```text
fixture appears
→ PREMATCH
→ prematch markets / selections / odds updates
→ kickoff
→ LIVE
→ live score/state/markets/odds
→ MATCH_ENDED
→ final state
→ cleanup
```

Uzun vadeli sistem:

```text
Fixture discovery
+ Prematch state
+ Live state
+ Lifecycle transition
+ Cleanup
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
uv run python -m py_compile <filename.py>
```

Son bilinen regression baseline:

```text
47 passed
```

Önceki örnek:

```text
47 passed in 0.08s
47 passed in 0.09s
```

Bu baseline korunmalı.

---

# 3. CURRENT PROJECT STRUCTURE

Yaklaşık yapı:

```text
mystake-collector/
├── main.py
├── inspect_prematch_games.py
├── pyproject.toml
│
├── mystake/
│   ├── config.py
│   │
│   ├── events/
│   │   ├── __init__.py
│   │   ├── models.py
│   │   └── mapper.py
│   │
│   ├── pipeline/
│   │   ├── cache_decoder.py
│   │   ├── notification_processor.py
│   │   └── live_snapshot_diff.py
│   │
│   └── sources/
│       ├── cache/
│       │   └── client.py
│       │
│       └── mqtt/
│           ├── client.py
│           ├── message.py
│           └── protocol.py
│
└── tests/
    ├── events/
    │   └── test_mapper.py
    │
    ├── pipeline/
    │   ├── test_cache_decoder.py
    │   ├── test_live_snapshot_diff.py
    │   └── test_notification_processor.py
    │
    └── sources/
        └── mqtt/
            ├── test_client.py
            ├── test_protocol.py
            └── test_publish.py
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

`mystake/sources/mqtt/client.py` içine şunları koyma:

```text
domain diff
fixture lifecycle
DB writes
odds normalization
semantic event generation
business logic
```

MQTT client yalnızca protocol / transport seviyesinde kalmalı.

---

# 5. MQTT CONNECTION — LIVE PROVEN

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

Örnek:

```text
Connecting to MyStake MQTT WebSocket
WebSocket connected. subprotocol=mqtt
MQTT CONNACK accepted
MQTT keepalive enabled
```

---

# 6. LIVE MQTT TOPIC — PROVEN

Exact topic:

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

ile reddetti.

Dolayısıyla wildcard live discovery yok.

---

# 7. MQTT PUBLISH / CACHE MODEL

MQTT PUBLISH çoğunlukla doğrudan data taşımıyor.

Payload örneği:

```text
cache:https://.../api/cache/get?key=...
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

Bu pipeline çalışıyor.

---

# 8. NotificationProcessor IMPORTANT DETAIL

`NotificationProcessor.process(message)` doğrudan dict dönmüyor.

Return type:

```text
ProcessedNotification
```

Decoded payload:

```python
processed.data
```

üzerinden alınmalı.

Bu daha önce yapılan önemli bir düzeltmeydi.

---

# 9. LIVE SNAPSHOT — PROVEN

Live snapshot içinde önemli root alanları:

```text
gmk
TimeLines
Match
mk
```

Live diff/event logic implement edildi.

---

# 10. LIVE SEMANTICS — PROVEN / OBSERVED

## visible=false

```text
visible=false
```

tek başına bütün market suspended demek değildir.

## LiveBetStatus=false

```text
LiveBetStatus=false
```

tek başına match ended değildir.

Geçici suspension olabilir.

## Football match-end pattern

Gerçek maçta güçlü pattern:

```text
Status == 3
BetStatus == 0
EventStatus == 40
```

`LiveBetStatus=false` eşlik edebilir ama tek başına kullanılmamalı.

---

# 11. REAL LIVE MATCH END TEST

Gerçek GameId:

```text
75297868
```

yaklaşık 88. dakikadan maç sonuna kadar izlendi.

Şunlar doğrulandı:

```text
subscribe
→ live updates
→ match-end detection
→ final state
→ cleanup
```

---

# 12. MQTT RECONNECT / UNSUBSCRIBE — PROVEN

Gerçek broker üzerinde doğrulandı:

```text
CONNECT
SUBSCRIBE
SUBACK
PUBLISH
PINGREQ/PINGRESP
reconnect
resubscribe
UNSUBSCRIBE
UNSUBACK
```

ÖNEMLİ:

`unsubscribe()` implementasyonunda pending packet queue var.

Sebebi:

Broker UNSUBACK gelmeden önce PUBLISH gönderebiliyor.

Örnek olası sequence:

```text
PUBLISH
PUBLISH
UNSUBACK
```

Pending packet queue kaldırılmamalı.

---

# 13. PREMATCH DISCOVERY TOPIC — PROVEN

Prematch topic:

```text
prematch/games
```

Exact subscribe çalışıyor.

Decoded payload shape:

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

# 14. PREMATCH/GAMES SEMANTICS

`UpdateList` full fixture universe değildir.

Change / notification stream gibi davranıyor.

Aynı payload tekrar gelebiliyor.

Exact duplicate suppression implement edildi.

Örnek:

```text
Duplicate : True
Skipping exact duplicate notification.
```

---

# 15. UpdateTimeStamp ODDITY

`UpdateTimeStamp` bazen:

```text
13 digit millisecond-like
```

bazen:

```text
10 digit second-like
```

geliyor.

Henüz normalize edilmedi.

Monotonic sequence gibi güvenilmemeli.

Semantiği kesin değil.

---

# 16. PREMATCH FULL ENDPOINT — PROVEN

Endpoint:

```text
https://analytics-sp.googleserv.tech/api/prematch/getprematchgamefull/28/{GameId}
```

Örnek:

```text
https://analytics-sp.googleserv.tech/api/prematch/getprematchgamefull/28/75747467
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

Kesin anlamı bilinmiyor.

Tahminle isim verme.

---

# 17. GETPREMATCHGAMEFULL RESPONSE FORMAT

Outer payload:

```json
{
  "game": "...JSON string...",
  "price": "[]",
  "disableMarkets": null
}
```

`game` JSON-encoded string.

Decode:

```python
outer = response.json()
game = json.loads(outer["game"])
```

---

# 18. PREMATCH GAME FIELDS

Örnek GameId:

```text
75747467
```

Fields:

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

Kanıt olmadan bu kısa field’lara semantic isim uydurma.

---

# 19. PREMATCH MARKET STRUCTURE

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

Player markets:

```text
p1
pl
```

taşıyabiliyor.

---

# 20. GETPREMATCHGAMEALL — PROVEN

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

Response encoding nested JSON string olabilir.

Decode chain:

```text
response.json()
→ maybe string
→ json.loads(...)
→ dict
→ nested game JSON string
→ nested teams JSON string
```

Top-level:

```text
game
outrights
companyPrice
teams
```

---

# 21. TEAM ID → NAME — PROVEN

Örnek:

```text
GameId=75747467
t1=10460
t2=10473
```

teams:

```text
10460 -> Flamengo RJ
10473 -> Red Bull Bragantino SP
```

Başka:

```text
15347 -> Pays-Bas
15252 -> Allemagne
```

Mapping:

```text
game.t1 / game.t2
→ teams[].ID
→ teams[].Name
```

çalışıyor.

---

# 22. GETPREMATCHGAMEALL BATCH SUPPORT — PROVEN

Bir request içinde birden fazla GameId gönderilebiliyor.

Örnek:

```text
?games=,75433979,75747467
```

Sonuç:

```text
requested games: 2
returned games : 2
teams          : 4
```

Bu önemli.

---

# 23. MULTI-SPORT STREAM — PROVEN

`prematch/games` birden fazla sport taşıyor.

Observed sport IDs:

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

Bazıları biliniyor:

```text
sport=1 → football-looking
sport=3 → baseball example
sport=5 → tennis examples
```

Ama bilinmeyen sport ID’lere tahminle isim verme.

---

# 24. CRITICAL DISCOVERY — gameall IS PARTIAL

Aynı GameId:

```text
75747467
```

için comparison yapıldı.

## getprematchgameall

```text
bytes       = ~9 KB
mc          = 384
pc          = 6793
ev markets  = 6
ev selects  = 81
```

## getprematchgamefull

```text
bytes       = ~782 KB
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

Definitive model:

```text
getprematchgameall
=
PARTIAL / DELTA payload
```

```text
getprematchgamefull
=
FULL SNAPSHOT
```

---

# 25. IMPORTANT RULE — MISSING MARKET DOES NOT MEAN REMOVED

Bir `gameall.ev` içinde market yoksa:

```text
market removed
```

demek değildir.

Sadece:

```text
this market was not part of this partial update
```

olabilir.

Dolayısıyla gameall payload’larını full snapshot gibi karşılaştırma.

---

# 26. PREMATCH STATE ARCHITECTURE — CURRENT MODEL

İlk kez görülen GameId:

```text
FIRST SEEN
→ getprematchgamefull
→ FULL SNAPSHOT bootstrap
```

Sonraki update:

```text
known GameId
→ getprematchgameall
→ partial delta
→ merge into local full state
```

Sonra:

```text
previous full state
vs
new merged full state
→ logical diff
```

State şüpheli ise:

```text
getprematchgamefull
→ full resync
```

---

# 27. ORIGINAL MERGE ATTEMPT

İlk yaklaşım:

```text
delta market
→ selection-by-selection upsert
```

idi.

Yani:

```text
old market selections preserved
new selection IDs added/replaced
```

Bu gerçek trafikte bazı durumlarda yanlış çıktı.

Özellikle aynı market içinde eski selections silinip yeni selections gelince eski selection’lar local state’te kalıyordu.

---

# 28. CRITICAL DISCOVERY — MARKET-LEVEL REPLACEMENT

Gerçek loglardan görüldü:

Bir market `gameall.ev` içinde GELİYORSA, o market’in selection dictionary’si current set gibi davranıyor.

Bu yüzden merge değiştirildi.

Current merge rule:

```text
market absent in delta
→ preserve existing market

market present in delta
→ replace complete local market with delta market
```

Kod mantığı:

```python
for market_id, delta_selections in delta["ev"].items():
    merged["ev"][market_id] = deepcopy(delta_selections)
```

Bu selection-by-selection upsert yerine kullanılıyor.

---

# 29. TOP-LEVEL SCALAR MERGE RULE

Delta içinde gelen scalar fields:

```text
up
pc
mc
st
...
```

mevcut state üzerine replace ediliyor.

`ev` ayrı ele alınıyor.

---

# 30. pc CONSISTENCY CHECK — IMPORTANT

`pc` ile full selection count arasında çok güçlü ilişki gözlendi.

Örnek:

```text
gamefull pc = 6793
count(full ev selections) = 6793
```

Bu yüzden merge sonrası:

```text
local selection count
```

ile:

```text
server pc
```

karşılaştırılıyor.

Rule:

```text
local_count == pc
→ accept merged state

local_count != pc
→ state suspect
→ getprematchgamefull full resync
```

Bu fallback kesinlikle korunmalı.

---

# 31. WHY pc CHECK IS NEEDED

Bazı `gameall` delta’ları global state change’i tam açıklamıyor.

Örnek:

```text
old local = 402
new server pc = 407
```

ama delta içinde gerekli tüm additions olmayabiliyor.

Bu durumda local merge:

```text
402
```

kalabiliyor.

Mismatch:

```text
local=402
server_pc=407
```

görülüyor.

Full resync sonrası:

```text
407
```

geliyor.

Yani `gameall` her durumda full logical delta olmayabilir.

---

# 32. OTHER MISMATCH TYPE — STATE COLLAPSE

Gerçek örnek:

```text
GameId=76457372
```

Mismatch sonrası full resync:

```text
Resynced selections: 0
Resynced pc        : 0
```

Bu fixture’ın prematch betting state’i tamamen boşalmış.

Ama bunun kesin lifecycle anlamı bilinmiyor.

Şunlardan biri olabilir:

```text
market close
fixture removal
kickoff transition
temporary state collapse
other protocol state
```

Kanıt olmadan isim verme.

---

# 33. ANOTHER MISMATCH EXAMPLE

Gerçek örnek:

```text
GameId=76456861
```

Mismatch sonrası:

```text
Resynced selections: 88
Resynced pc        : 88
```

Burada state tamamen kapanmamış.

Bu mismatch farklı sebepten olmuş olabilir.

Dolayısıyla tüm mismatch’leri aynı kategori kabul etme.

---

# 34. REAL PRICE CHANGES — LIVE PROVEN

Current delta merge ile gerçek prematch odds changes yakalandı.

Örnek:

```text
coef: 1.82 -> 1.90
coef: 1.92 -> 1.84
```

Başka örnekler:

```text
2.17 -> 2.12
1.59 -> 1.62
```

Bu chain artık gerçek trafik üzerinde kanıtlandı:

```text
prematch/games notification
→ getprematchgameall
→ partial delta
→ market-level replacement merge
→ merged full state
→ logical diff
→ real coef change
```

---

# 35. REAL SELECTION ADDITIONS — LIVE PROVEN

Bir gerçek örnekte:

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

yakalandı.

Merge sonrası:

```text
local=18
server_pc=18
```

oldu.

Bu market replacement yaklaşımını güçlü şekilde destekliyor.

---

# 36. REMOVALS — OBSERVED

Full resync sonrası:

```text
removed markets
removed selections
```

çok sayıda görüldü.

Ancak önemli distinction:

```text
removed in final full-state diff
```

ile:

```text
explicit removal command in gameall
```

aynı şey değildir.

Şu anda gameall removal semantics hâlâ tam çözülmüş değil.

---

# 37. LOCK CHANGES

Son soak testte:

```text
Lock changes = 0
```

çıktı.

Bu:

```text
lock field hiç değişmez
```

anlamına gelmez.

Sadece o observation window’da lock change yakalanmadı.

---

# 38. DELETE LIST

`DeleteList` bugüne kadar çoğunlukla:

```text
[]
```

geldi.

Exact semantics hâlâ bilinmiyor.

DeleteList gelirse şimdilik observe/log et.

Kanıt olmadan state silme.

---

# 39. CURRENT DEVELOPMENT RUNNER

Root file:

```text
inspect_prematch_games.py
```

Bu production implementation değil.

Reverse-engineering / protocol validation runner.

Şu anda runner şunları yapıyor:

```text
subscribe prematch/games
→ decode UpdateList
→ duplicate suppression
→ batch getprematchgameall
→ team metadata
→ first seen: gamefull bootstrap
→ known: partial delta merge
→ market-level replacement
→ count local selections
→ compare with pc
→ mismatch: full resync
→ diff full states
→ print logical changes
→ stats
```

---

# 40. CURRENT IN-MEMORY STATE

State store:

```python
game_states: dict[GameId, full_game_state]
```

Team metadata:

```python
team_map: dict[TeamId, TeamName]
```

Duplicate suppression:

```python
previous_notification_payload
```

---

# 41. SOAK TEST STATS — MOST IMPORTANT RECENT RESULT

Son 5 dakikalık soak test sonucu:

```text
====================================================================================================
PREMATCH SOAK TEST STATS
====================================================================================================
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
====================================================================================================
```

Bu en önemli son checkpoint.

---

# 42. SOAK TEST INTERPRETATION

258 known-game delta merge içinde:

```text
249 count match
9 mismatch
```

Direct selection-count match:

```text
96.51%
```

Bu market-level replacement modelinin güçlü şekilde çalıştığını gösteriyor.

Ancak:

```text
count match
```

full exact-state match demek değildir.

Bu çok önemli.

---

# 43. CRITICAL LIMITATION OF pc CHECK

Şu scenario mümkün:

```text
old selections:
A
B
C
D

new correct selections:
A
B
E
F
```

Count:

```text
old = 4
new = 4
pc = 4
```

Local state yanlış selection IDs tutsa bile:

```text
local_count == pc
```

olabilir.

Dolayısıyla count check state correctness için yeterli değildir.

Bu, bir sonraki deneyin ana sebebidir.

---

# 44. CURRENT KNOWN GOOD MERGE MODEL

Şu anda en iyi evidence-based model:

```text
FIRST SEEN
→ gamefull

KNOWN GAME
→ gameall partial

top-level scalar fields present
→ replace

market absent in delta
→ preserve

market present in delta
→ replace whole local market

after merge:
local selection count == pc ?
    YES → tentatively accept
    NO  → gamefull resync
```

Bu model korunmalı.

---

# 45. CURRENT CONFIDENCE

Şu noktalar güçlü şekilde proven:

```text
getprematchgameall is partial                      ✅
getprematchgamefull is full                        ✅
gameall supports batch                             ✅
team metadata mapping works                        ✅
full bootstrap works                               ✅
market-level replacement works far better          ✅
real odds changes detected                         ✅
selection additions detected                       ✅
pc count mismatch catches many incomplete states   ✅
full resync repairs mismatch                       ✅
```

Henüz proven değil:

```text
count-match == exact state                         ❌
all removal semantics                              ❌
DeleteList semantics                               ❌
pc semantics formally                              ❌
mc semantics                                       ❌
market removal transport semantics                 ❌
prematch→live transition                           ❌
persistent fixture lifecycle                       ❌
```

---

# 46. NEXT IMMEDIATE GOAL

Bir sonraki chat’te İLK HEDEF:

```text
SAMPLED FULL-STATE VALIDATION
```

Amaç:

`pc` count match olmuş olsa bile local merged state gerçekten `gamefull` ile aynı mı?

Bunu test edeceğiz.

---

# 47. SAMPLED FULL-STATE VALIDATION DESIGN

Her successful delta merge’de `gamefull` çekmek istemiyoruz.

Çünkü full endpoint büyük:

```text
~hundreds of KB
```

Bazı fixtures:

```text
~782 KB
```

olabiliyor.

Bu yüzden sample validation yapılmalı.

Önerilen ilk test:

```text
every 20 successful count-matched delta merges
→ fetch gamefull
→ compare local merged state vs full authoritative state
```

Yani yaklaşık:

```text
SAMPLE_EVERY_COUNT_MATCHES = 20
```

gibi.

---

# 48. SAMPLE VALIDATION RULE

Flow:

```text
delta merge
→ local_count == pc
→ successful count match
→ every Nth successful merge:
      fetch gamefull
      compare
```

Ama race condition olabilir.

Önemli:

`gamefull` fetch edilene kadar server state bir kez daha değişebilir.

Bu yüzden özellikle:

```text
merged.up
vs
full.up
```

karşılaştır.

Eğer:

```text
merged.up != full.up
```

ise:

```text
validation inconclusive due to race
```

olarak işaretle.

Exact-state failure sayma.

---

# 49. EXACT STATE COMPARISON

Eğer:

```text
merged.up == full.up
```

ise karşılaştır:

```text
local merged state
vs
fresh gamefull state
```

Özellikle:

```text
ev market IDs
selection IDs
selection payloads
scalar fields
```

karşılaştır.

Ama gerekirse noisy / transport-only fields ayrıca incelenebilir.

İlk testte exact canonical comparison yapılabilir.

---

# 50. DESIRED VALIDATION STATS

Runner’a sonraki adımda şu sayaçları eklemek mantıklı:

```text
Sampled validations
Sampled exact matches
Sampled mismatches
Sampled races/inconclusive
```

Örnek:

```text
Sampled validations        : 15
Sampled exact matches      : 14
Sampled mismatches         : 0
Sampled race/inconclusive  : 1
```

Sonra metric:

```text
Sampled exact-state rate
```

hesaplanabilir.

---

# 51. IF SAMPLED MISMATCH HAPPENS

Count:

```text
local == pc
```

ama full snapshot farklıysa çok önemli discovery.

Diagnostic yazdır:

```text
GameId
Match

merged up
full up

merged pc
full pc

merged market count
full market count

merged selection count
full selection count

markets only local
markets only full

for common markets:
    selections only local
    selections only full

same selection ID but changed payload fields
```

Bu durumda hangi state dimension’ın yanlış olduğunu çöz.

---

# 52. POSSIBLE SAMPLE MISMATCH CLASSES

Muhtemel kategoriler:

```text
SAME_COUNT_SELECTION_REPLACEMENT

MARKET_SET_MISMATCH

SELECTION_PAYLOAD_MISMATCH

SCALAR_FIELD_MISMATCH

RACE / INCONCLUSIVE
```

Bunları ancak gerçek örnek geldikten sonra resmileştir.

---

# 53. CURRENT MOST IMPORTANT OPEN QUESTIONS

Sırayla:

```text
1. Count-match state gerçekten exact gamefull ile eşleşiyor mu?
2. gameall market replacement her market için doğru mu?
3. same-count selection replacement kaçabiliyor mu?
4. market removal gameall içinde nasıl ifade ediliyor?
5. selection removal gameall içinde nasıl ifade ediliyor?
6. DeleteList ne zaman geliyor?
7. pc exact semantics nedir?
8. mc exact semantics nedir?
9. prematch fixture ne zaman live olur?
10. prematch GameId == live GameId mi?
11. kickoff transition nasıl keşfediliyor?
12. fixture finished/removal lifecycle nasıl işliyor?
```

---

# 54. FUTURE PREMATCH → LIVE GOAL

Çok önemli ileriki test:

Bir fixture prematch state’te takip edilirken kickoff geldiğinde:

```text
same GameId
```

şurada kullanılabiliyor mu?

```text
live/gamenew/{GameId}
```

Ana soru:

```text
PREMATCH GameId == LIVE GameId ?
```

Bunu gerçek fixture üzerinde kanıtlamak gerekiyor.

Kanıt olmadan true kabul etme.

---

# 55. EVENTUAL FULL FLOW

Hedeflenen architecture:

```text
prematch/games
    ↓
Fixture discovery/change signal
    ↓
getprematchgameall
    ↓
partial market updates
    ↓
local full prematch state
    ↓
gamefull bootstrap / resync when needed
    ↓
prematch logical events
    ↓
kickoff
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

# 56. FUTURE PRODUCTION MODULES

Protocol yeterince kanıtlandıktan sonra muhtemel yapı:

```text
mystake/
├── sources/
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

Ama HENÜZ production refactor yapma.

Sampled validation tamamlanmadan acele etme.

---

# 57. FUTURE FIXTURE REGISTRY

Protocol netleşince registry şu tip state taşıyabilir:

```text
GameId
sport
region
championship
team1
team2
start_time
prematch_state
live_state
lifecycle_status
last_update
```

Possible lifecycle:

```text
DISCOVERED
PREMATCH
LIVE
ENDED
REMOVED
```

Ama enumlar final değil.

---

# 58. DO NOT YET BUILD

Henüz:

```text
PostgreSQL
Redis
Kafka
RabbitMQ
production DB schema
persistent registry
Cloud deployment
Java rewrite
large refactor
semantic event explosion
```

yapma.

Önce protocol correctness.

---

# 59. IMPORTANT PERFORMANCE NOTE

`gamefull` çok büyük olabilir.

Örnek:

```text
~782 KB
```

Bu nedenle production’da her delta için full fetch yapılmamalı.

Ana model zaten:

```text
bootstrap with gamefull
then use lightweight gameall partial updates
fallback to gamefull only when needed
```

Sample validation sadece reverse-engineering için temporary test.

---

# 60. STATS RUNNER CURRENT OUTPUT MODEL

Current runner stats:

```text
Notifications
Duplicate notifications
Delta batches
Delta games
Full bootstraps
Delta merges
Count matches
Count mismatches
Full resyncs
Logical changes
No-op updates
Price changes
Lock changes
Added markets
Removed markets
Added selections
Removed selections
Direct merge count-match %
```

Sampled validation eklenince buna yenileri eklenecek.

---

# 61. IMPORTANT OBSERVATION ABOUT NO-OP UPDATES

Son soak:

```text
Logical changes : 108
No-op updates   : 150
```

Yani prematch notification stream oldukça noisy.

Her notification semantic event üretmemeli.

Full logical diff gerekli.

---

# 62. IMPORTANT OBSERVATION ABOUT DUPLICATES

Son soak:

```text
Notifications           : 185
Duplicate notifications : 81
```

Yaklaşık ciddi bir kısmı exact duplicate.

Duplicate suppression kesinlikle değerli.

---

# 63. IMPORTANT OBSERVATION ABOUT CHANGES

Son soak:

```text
Price changes      : 475
Added selections   : 16
Removed selections : 54
Removed markets    : 108
```

Bu runner gerçek prematch state movement’i yakalıyor.

Ancak `Removed markets=108` gibi sayıların büyük kısmı full resync sonrası görülen state contraction’dan gelebilir.

Transport-level explicit delete diye yorumlama.

---

# 64. CURRENT EVIDENCE-BASED RULES SUMMARY

Şunları bozma:

```text
RULE 1
gameall is partial at game level.

RULE 2
missing market in gameall is NOT removal.

RULE 3
market present in gameall currently behaves like a full market selection set.

RULE 4
replace that whole market locally.

RULE 5
top-level scalar fields present in delta replace local fields.

RULE 6
pc mismatch means state is suspect.

RULE 7
on pc mismatch → gamefull resync.

RULE 8
pc match does NOT guarantee exact-state correctness.

RULE 9
sampled gamefull validation is next experiment.

RULE 10
race must be detected using up or equivalent version signal before calling validation failed.
```

---

# 65. TOMORROW — FIRST TASK

Yeni chat açıldığında ilk görev:

```text
Modify inspect_prematch_games.py to add sampled full-state validation.
```

Ama sadece mevcut working behavior üzerine ekle.

Merge algoritmasını değiştirme.

Önerilen sample:

```python
VALIDATE_EVERY_COUNT_MATCHES = 20
```

Possible stats additions:

```text
sampled_validations
sampled_exact_matches
sampled_mismatches
sampled_races
```

---

# 66. TOMORROW — EXPECTED TEST FLOW

Önce:

```bash
uv run python -m py_compile inspect_prematch_games.py
```

Sonra:

```bash
uv run pytest
```

Expected:

```text
47 passed
```

Sonra:

```bash
uv run python inspect_prematch_games.py
```

Yaklaşık:

```text
5–10 minutes
```

çalıştır.

Sonra:

```text
Ctrl + C
```

Final stats alınacak.

---

# 67. SAMPLE VALIDATION OUTPUT IDEA

Successful validation:

```text
SAMPLED FULL VALIDATION
GameId=...

merged up : 178...
full up   : 178...

RESULT: EXACT MATCH
```

Race:

```text
SAMPLED FULL VALIDATION
GameId=...

merged up : 178...10
full up   : 178...11

RESULT: INCONCLUSIVE / STATE ADVANCED
```

Mismatch:

```text
SAMPLED FULL VALIDATION MISMATCH

markets only local:
...

markets only full:
...

selections only local:
...

selections only full:
...
```

Bu sadece suggested shape.

Exact implementation yarın yapılabilir.

---

# 68. IMPORTANT SAMPLE VALIDATION CAUTION

`up` field saniye resolution benzeri görünüyor olabilir.

Bu yüzden:

```text
same up
```

mutlak race-free guarantee olmayabilir.

Ama ilk validation gate olarak kullanılabilir.

Eğer exact mismatch yakalanır ama `up` aynıysa bile bunun race olma ihtimali ayrıca düşünülmeli.

Gerekirse request timing / immediate re-fetch / second full confirmation yapılabilir.

Bunu ileride gerçek mismatch gelirse değerlendir.

---

# 69. KNOWN IMPORTANT FIXTURES

## 75747467

```text
Flamengo RJ
vs
Red Bull Bragantino SP
```

Important because:

```text
gameall vs gamefull strict subset proof
```

yapıldı.

## 74679515

```text
Milan AC
vs
US Lecce
```

Repeated updates gözlendi.

Ayrıca daha önce:

```text
live/gamenew/74679515
```

ile live testte kullanıldı.

Bu prematch/live mapping için ileride değerli olabilir.

## 75297868

Live match end’e kadar takip edilen fixture.

## 76457372

State collapse example:

```text
resync selections = 0
pc = 0
```

## 76456861

Mismatch sonrası:

```text
resync selections = 88
pc = 88
```

---

# 70. USER WORKING STYLE

Kullanıcı:
- PyCharm kullanıyor.
- macOS terminal kullanıyor.
- Komutları kendisi çalıştırıyor.
- Kod değişikliklerinde mümkünse complete file istiyor.
- Adım adım ilerlemeyi tercih ediyor.
- Önce terminal çıktısını gönderip sonra bir sonraki adıma geçmek istiyor.

Cevaplar:
- Türkçe.
- Exact.
- Gereksiz teori yok.
- Her seferinde tek development objective.
- Exact filenames.
- Exact commands.
- Expected result.

---

# 71. DO NOT FORGET THIS

Şu an production implementation yapmıyoruz.

Bu aşama:

```text
reverse engineering
protocol proving
state-model validation
```

Bir davranış gerçek trafik üzerinde yeterince kanıtlanmadan production katmanına taşımıyoruz.

---

# 72. PROJECT MILESTONE STATUS

```text
MQTT connection                         ✅
MQTT framing                            ✅
CONNACK                                 ✅
SUBSCRIBE / SUBACK                      ✅
PUBLISH                                 ✅
keepalive                               ✅
reconnect                               ✅
resubscribe                             ✅
unsubscribe                             ✅

cache URL notifications                 ✅
HTTP cache fetch                        ✅
base64/gzip/json decode                 ✅

live exact topic                        ✅
live state decode                       ✅
live diff                               ✅
semantic live events                    ✅
live match end                          ✅
live cleanup                            ✅

prematch/games subscribe                ✅
UpdateList                              ✅
GameId discovery                        ✅
duplicates                              ✅

getprematchgameall                      ✅
batch gameall                           ✅
team metadata                           ✅
multi-sport stream                      ✅

getprematchgamefull                     ✅
full snapshot                           ✅

gameall is partial                      ✅ PROVEN
gamefull is full                        ✅ PROVEN

full bootstrap                          ✅
market-level delta replacement          ✅ STRONGLY SUPPORTED
real prematch price changes             ✅ PROVEN
selection additions                     ✅ PROVEN
count-based integrity check              ✅
full resync fallback                    ✅
5-min soak test                         ✅

direct count-match rate                 ✅ 96.51%

exact-state validation                  ⏳ NEXT
same-count hidden divergence             ⏳ NEXT
market removal semantics                ⏳
selection removal semantics             ⏳
DeleteList semantics                    ⏳
pc exact semantics                      ⏳
mc exact semantics                      ⏳
prematch→live transition                ⏳
persistent fixture registry             ⏳ LATER
storage                                 ⏳ LATER
```

---

# 73. LAST VERIFIED SOAK TEST RESULT

Keep this exact result as latest checkpoint:

```text
====================================================================================================
PREMATCH SOAK TEST STATS
====================================================================================================
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
====================================================================================================
```

---

# 74. TOMORROW — START HERE

Yarın yeni chat’te bu document’i yapıştırdıktan sonra şunu söyle:

```text
Devam edelim. İlk görevimiz sampled full-state validation.
Mevcut inspect_prematch_games.py merge davranışını değiştirmeden,
her N başarılı count-match merge’den birini gamefull ile validate edelim.
Race/inconclusive ayrımını da ekleyelim.
Önce exact implementation planı, sonra complete file ver.
```

---

# 75. FINAL CURRENT MODEL

Şu anda elimizdeki en sağlam prematch state pipeline:

```text
prematch/games
        ↓
UpdateList GameIds
        ↓
getprematchgameall batch
        ↓
PARTIAL GAME UPDATE
        ↓
first seen?
   YES → gamefull bootstrap
   NO  → merge
              ↓
      scalar replace
      market-present → full market replace
      market-absent  → preserve
              ↓
      count selections
              ↓
      local == pc ?
         YES → accept provisionally
         NO  → gamefull resync
              ↓
      logical full-state diff
              ↓
      price / selection / market changes
```

Bir sonraki deney bunun üzerine şu katmanı ekleyecek:

```text
accepted count-match merge
        ↓
sample occasionally
        ↓
fetch gamefull
        ↓
same version?
    NO → race / inconclusive
    YES → exact state compare
        ↓
    MATCH / MISMATCH
```

Bu validation başarılı olursa prematch merger’ı production module’a taşımaya çok yaklaşmış olacağız.