# MyStake Collector — Complete Continuation Context

Bu doküman `mystake-collector` projesine yeni bir ChatGPT session’ında kaldığımız yerden eksiksiz devam etmek için hazırlanmıştır.

## Çalışma prensibi

Ana yaklaşım:

```text
observe
→ experiment
→ prove
→ only then productionize
```

Protocol semantics tahmin edilmemeli.

Kanıtlanmış davranış ile hipotez birbirinden ayrılmalı.

Şimdilik production refactor, database, Redis, Kafka/RabbitMQ veya Java rewrite yapılmayacak.

Öncelik:

```text
protocol correctness
```

Kullanıcı:

```text
macOS
PyCharm
Python 3.13
uv
Chrome DevTools
```

Adım adım ilerlemek istiyor.

Bir kerede tek deney/görev ver.

Terminal/DevTools çıktısını kullanıcı gönderiyor, sonra sonraki adıma geçiliyor.

---

# 1. PROJECT

Project:

```text
mystake-collector
```

Amaç MyStake’tan browser bağımsız şekilde tüm lifecycle verisini toplamak:

```text
fixture discovery
prematch state
prematch markets
prematch selections
prematch odds

kickoff

live state
live markets
live odds
live score

match end
final state
cleanup
```

Sadece canlı maçlar takip edilmeyecek.

Full lifecycle hedefi:

```text
fixture appears
→ PREMATCH
→ prematch updates
→ kickoff
→ LIVE
→ live updates
→ MATCH_ENDED
→ cleanup
```

---

# 2. DEVELOPMENT / BASELINE

Project root:

```bash
cd ~/Desktop/Projects/mystake-collector
```

Test:

```bash
uv run pytest
```

Mevcut regression baseline:

```text
47 passed
```

Ana prematch inspection:

```bash
uv run python inspect_prematch_games.py
```

Main:

```bash
uv run python main.py
```

Baseline bozulmamalı.

---

# 3. CURRENT STRUCTURE

Kabaca:

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

Architecture rule:

```text
sources/
→ transport/protocol

pipeline/
→ decoding / transformation / diff

events/
→ semantic events
```

MQTT client içine business logic koyma.

---

# 4. MQTT — PROVEN

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

Reconnect/resubscribe gerçek broker üzerinde çalıştı.

UNSUBSCRIBE/UNSUBACK gerçek broker üzerinde doğrulandı.

Wildcard:

```text
live/gamenew/#
```

reddedildi:

```text
SUBACK 0x80
```

Exact live topic:

```text
live/gamenew/{GameId}
```

çalışıyor.

---

# 5. MQTT CACHE MODEL — PROVEN

PUBLISH payload çoğu zaman doğrudan data taşımıyor.

Örnek:

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

Bu implement edildi.

---

# 6. LIVE SIDE — PROVEN

Live snapshot alanları gözlendi.

Live diff çalışıyor.

Semantic events üretilebiliyor.

Football için güçlü match-end pattern:

```text
Status == 3
BetStatus == 0
EventStatus == 40
```

Gerçek maçta:

```text
subscribe
→ updates
→ match-end detection
→ final state
→ cleanup
```

doğrulandı.

---

# 7. PREMATCH MQTT TOPICS — PROVEN

Global topics:

```text
prematch/header
prematch/games
prematch/markets
```

Üçü de gerçek MQTT SUBSCRIBE/PUBLISH ile doğrulandı.

GameId-specific prematch MQTT topic henüz bulunmadı.

---

# 8. PREMATCH/GAMES — PROVEN

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

Bu full fixture universe değil.

Daha çok:

```text
change / update / invalidation stream
```

gibi davranıyor.

Exact duplicate notification’lar geliyor.

Duplicate suppression implement edildi.

`DeleteList` çoğunlukla boş gözlendi.

Semantiği henüz kanıtlanmadı.

Geldiğinde doğrudan delete etme.

Önce logla ve kanıtla.

---

# 9. UpdateTimeStamp

Daha önce gözlendi:

```text
13-digit
```

ve:

```text
10-digit
```

olabiliyor.

Önceden semantiği belirsizdi.

Bu son HAR testi ile artık `gamefull.game.up` ile çok güçlü ilişki bulundu.

Detay aşağıda.

---

# 10. PREMATCH/MARKETS

Topic:

```text
prematch/markets
```

Cache response actual market data değil.

Örnek:

```text
"MTc4OTk3ODUwMw=="
```

base64 decode:

```text
1789978503
```

Sonraki:

```text
1789979103
```

Fark:

```text
600 seconds
```

Yani tam 10 dakika.

Strong hypothesis:

```text
prematch/markets
→ timestamp/version/invalidation marker
```

Actual odds payload olmadığı açık.

Exact semantik adı henüz kesin verilmemeli.

---

# 11. PREMATCH ENDPOINTS

Full:

```text
https://analytics-sp.googleserv.tech/api/prematch/getprematchgamefull/28/{GameId}
```

Pattern:

```text
/api/prematch/getprematchgamefull/{CONTEXT_ID}/{GAME_ID}
```

Current:

```python
PREMATCH_CONTEXT_ID = 28
```

`28` sport ID değil.

Exact anlamı bilinmiyor.

---

# 12. GAMEFULL — PROVEN AUTHORITATIVE

Response outer:

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

`gamefull` authoritative full snapshot gibi davranıyor ve gerçek testlerle doğrulandı.

---

# 13. GAMEALL

Endpoint:

```text
/api/prematch/getprematchgameall/{LANGUAGE}/28/?games=,{GAME_IDS}
```

Batch support var:

```text
?games=,75433979,75747467
```

Top-level nested JSON strings içeriyor.

---

# 14. GAMEALL VS GAMEFULL — PROVEN

Gerçek fixture karşılaştırması:

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
gameall = PARTIAL payload
gamefull = FULL authoritative snapshot
```

---

# 15. GAMEALL COMPLETE LOGICAL DELTA DEĞİL — PROVEN

İlk merge yaklaşımı:

```text
market absent from delta
→ preserve local market

market present in delta
→ replace entire local market
```

Ama soak testlerde authoritative divergence bulundu.

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

Ayrıca selection identity count değişmeden değişebiliyor.

Örneğin aynı market için:

```text
local IDs != full IDs
```

ama count aynı.

Conclusion:

```text
gameall
=
partial view / partial update

NOT authoritative snapshot
NOT complete logical delta
```

---

# 16. PC CHECK — YARARLI AMA YETERSİZ

Observed:

```text
gamefull.pc == total selection count
```

güçlü ilişki gösteriyor.

Bu yüzden:

```text
local selection count != pc
→ gamefull resync
```

eklendi.

Gerçek count mismatch resync çalıştı.

Ancak:

```text
pc match
≠ exact state correctness
```

PROVEN.

Same-count stable divergence bulundu.

Özellikle local/full count aynıyken odds farklı çıktı.

---

# 17. PERIODIC RECONCILIATION

Experimental:

```python
RECONCILE_EVERY_GAME_MERGES = 10
```

Flow:

```text
first seen
→ gamefull bootstrap

known game
→ gameall merge

every N merges
→ gamefull reconciliation
```

Reconciliation gerçek authoritative divergence’ları repair etti.

10 dakikalık soak:

```text
Reconciliation checks  : 11
matches                : 1
repairs                : 10
```

Yaklaşık:

```text
90.9% repair rate
```

Bu bize gameall merge modelinin exact state tutmadığını güçlü şekilde gösterdi.

---

# 18. ÖNEMLİ MİMARİ SONUÇ

Problemi:

```text
RECONCILE_EVERY_GAME_MERGES
10 → 5 → 1
```

yaparak brute force çözme.

Bu `gamefull` polling’e dönüşür.

Önce browser gerçek davranışı çözülmeliydi.

Bu nedenle Chrome HAR reverse engineering’e geçildi.

---

# 19. CLEAN PREMATCH TEST

Test GameId:

```text
76433701
```

URL:

```text
https://mystake.com/en/sportsbook/prematch/match/76433701
```

Test başlangıcı:

```text
local time ≈ 20:40
kickoff = 21:00
```

Yani yaklaşık 20 dakika temiz prematch observation window vardı.

Chrome DevTools:

```text
Network
Preserve log / Keep log ON
```

İlk filtre:

```regex
(getprematchgameall|getprematchgamefull).*76433701
```

Collector:

```bash
uv run python inspect_prematch_games.py
```

---

# 20. CRITICAL OBSERVATION — 20:55 EVENT

Collector:

```text
UPDATE GAME IDS
GameId=76295723 timestamp=1790013300 known=True
GameId=76373459 timestamp=1790013300 known=True
GameId=76433701 timestamp=1790013300 known=True

20:55:06.390
Fetching prematch DELTA batch games=3
```

Collector kendi logic’i nedeniyle:

```text
getprematchgameall/fr/28/?games=,76295723,76373459,76433701
```

çekti.

Bu browser call değil.

HAR export edildi ve analiz edildi.

HAR filename:

```text
dfab0916-2a3d-46a4-b5f3-ce03277e1f92.har
```

Yeni chat’te bu dosya yoksa kullanıcıdan tekrar istenebilir.

---

# 21. HAR — CRITICAL PROOF

HAR’dan görüldü:

```text
20:55:05.979
GET /api/cache/get?key=prematch/games
```

Decoded response:

```json
{
  "UpdateList": [
    {
      "GameId": 76295723,
      "UpdateTimeStamp": 1790013300
    },
    {
      "GameId": 76373459,
      "UpdateTimeStamp": 1790013300
    },
    {
      "GameId": 76433701,
      "UpdateTimeStamp": 1790013300
    }
  ],
  "DeleteList": []
}
```

Ardından browser:

```text
20:55:06.540
GET /api/prematch/getprematchgamefull/28/76433701
```

Fark:

```text
≈ 561 ms
```

Başka event:

```text
20:55:00.245
prematch/games
→ contains 76433701

20:55:00.528
gamefull/28/76433701

difference ≈ 283 ms
```

Bu aynı GameId için tekrarlandı.

---

# 22. GAMEFULL CALLS FOR 76433701

HAR’da toplam 7:

```text
20:55:00.528
20:55:06.540
20:55:30.869
20:55:35.135
20:57:01.035
20:57:06.265
20:57:06.822
```

Her birinin hemen öncesinde `prematch/games` içinde `76433701` bulunuyor.

Correlation:

```text
7 / 7
```

Bu çok güçlü kanıt.

---

# 23. GAMEFULL SNAPSHOT DIFF

İlk iki snapshot:

```text
20:55:00.528
20:55:06.540
```

Parsed state tamamen aynı:

```text
markets = 60 → 60
pc      = 267 → 267
mc      = 76 → 76
up      = 1790013300 → 1790013300
vis     = false → false

changed markets    = 0
changed selections = 0
coef changes       = 0
lock changes       = 0
```

Dolayısıyla:

```text
prematch/games update
≠ kesin odds değişikliği
```

Daha doğru model:

```text
prematch/games update
≈ authoritative refresh / invalidation signal
```

---

# 24. ACTUAL ODDS CHANGE SNAPSHOT

Sonraki snapshot:

```text
20:55:30.869
up = 1790013330
```

Öncekine göre:

```text
55 market changed
194 selections changed
194 coef changes
0 lock changes
0 added markets
0 removed markets
```

Örnek:

```text
Market 387:
6.55 → 6.81
2.37 → 2.39
1.98 → 1.94

Market 389:
1.79 → 1.82
1.98 → 1.94

Market 395:
2.15 → 2.17
1.58 → 1.56
```

Aynı state bazen birkaç saniye sonra tekrar fetch ediliyor.

---

# 25. STATE GROUPS

Observed:

```text
20:55:00 / 20:55:06
→ STATE A

20:55:30 / 20:55:35
→ STATE B

20:57:01 / 20:57:06 / 20:57:06
→ STATE C
```

Yani multiple invalidation/refresh çağrısı aynı authoritative state’i döndürebiliyor.

---

# 26. GAMEALL IN HAR

Bu HAR observation session’ında:

```text
getprematchgameall calls = 0
getprematchgamefull calls = 172
```

`76433701` için:

```text
gameall = 0
gamefull = 7
```

Bu HAR’daki realtime prematch detail refresh chain:

```text
prematch/games
→ gamefull
```

şeklinde.

`gameall` bu realtime refresh zincirinin parçası görünmüyor.

Gameall muhtemelen başka screen/use-case için kullanılıyor olabilir.

Henüz formal olarak hangi use-case olduğu bilinmiyor.

---

# 27. UpdateTimeStamp VS game.up

`76433701` için observed examples:

```text
UpdateTimeStamp      gamefull.game.up
--------------------------------------
1790013300367        1790013300
1790013300           1790013300
1790013330430        1790013330
1790013331           1790013330
1790013420602        1790013420
1790013421           1790013420
1790013421           1790013420
```

13-digit values normalize edilince aynı saniye family’sine düşüyor:

```text
1790013300367 ms
→ 1790013300.367 sec

game.up
→ 1790013300
```

Strong evidence:

```text
prematch/games.UpdateTimeStamp
≈ notification/update timestamp

gamefull.game.up
≈ authoritative state update/version timestamp
```

Aynı exact field oldukları henüz kesin değil.

Bazı örneklerde ~1 sec fark var.

---

# 28. GLOBAL HAR ANALYSIS

HAR genelinde:

```text
prematch/games update entries ≈ 5139
distinct GameIds ≈ 2006

gamefull calls = 172
distinct gamefull GameIds = 28
```

Bu çok önemli:

```text
prematch/games
= global update stream
```

Ama browser:

```text
all GameIds → gamefull
```

yapmıyor.

Sadece frontend açısından relevant/active olan subset için fetch yapıyor.

---

# 29. GLOBAL CORRELATION

172 total `gamefull` call analiz edildi.

Same GameId için preceding `prematch/games` update correlation:

```text
≤ 0.5 sec  → 88
≤ 1.0 sec  → 153
≤ 1.5 sec  → 162
≤ 2.0 sec  → 164
≤ 3.0 sec  → 167
```

Özellikle:

```text
164 / 172
≈ 95.3%
```

gamefull call, aynı GameId için önceki 2 saniye içindeki `prematch/games` update ile eşleşiyor.

Kalanlar çoğunlukla same-trigger fetch burst/repeated refresh pattern’ine benziyor.

---

# 30. GAMEID-LEVEL CORRELATION

28 farklı GameId’nin:

```text
28 / 28
```

en az bir `gamefull` çağrısından hemen önce aynı GameId’yi taşıyan `prematch/games` update’i var.

Daha detaylı:

```text
26 / 28 GameId
→ tüm gamefull calls ≤2 sec correlation

27 / 28 GameId
→ tüm gamefull calls ≤3 sec correlation
```

Main exception:

```text
76251894
```

Burada tek update ardından seri fetch burst var:

```text
+0.284 s
+0.566 s
+1.129 s
+1.696 s
+2.257 s
+2.823 s
+3.384 s
+3.943 s
+4.504 s
+5.074 s
+5.630 s
```

Bu independent triggers’dan çok repeated refresh/burst gibi görünüyor.

---

# 31. IMPORTANT FRONTEND MODEL

Current strongest evidence-based model:

```text
prematch/games
        ↓
global UpdateList
        ↓
frontend checks whether GameId is relevant/active
        ↓
if relevant
        ↓
getprematchgamefull/28/{GameId}
        ↓
replace/refresh authoritative state
```

IMPORTANT:

```text
prematch/games UpdateList entry
≠ guaranteed data change
```

Daha doğru interpretation:

```text
"This GameId should be revalidated/refreshed."
```

---

# 32. WHY ONLY SOME GAME IDS?

`prematch/games` 2006 farklı GameId taşıdı.

Ama browser sadece 28 farklı GameId için `gamefull` çekti.

Bu frontend’in bir:

```text
active / relevant fixture set
```

tuttuğunu güçlü şekilde gösteriyor.

HAR tek başına bunun UI nedenini kesin olarak söylemiyor.

Possible relevance mechanisms:

```text
visible fixtures
active league/competition
current sport
nearby kickoff window
detail page
application state
carousel/list
```

Henüz hangisi kesin bilinmiyor.

---

# 33. RELEVANT FIXTURE SET CLUES

Gamefull çekilen 28 GameId:

```text
sport=1
```

grubunda kümeleniyor.

Kickoff times da cluster gösteriyor.

Örnek time clusters:

```text
17:45 UTC
17:50 UTC
18:00 UTC
18:30 UTC
18:45 UTC
```

Ayrıca aynı `ch` değerine sahip bazı maçlar birlikte gamefull fetch ediliyor.

Örnek:

```text
ch=38220

76365207
76369418
76433701
76433705
76462118
```

Başka örnek:

```text
ch=7678

76250972
76295719
76295739
```

Başka:

```text
ch=57630

76449654
76449657
```

Başka:

```text
ch=69573

76396571
76396601
76396574
```

Bu şu hipotezi güçlendiriyor:

```text
frontend active sport/league/fixture groups
→ prematch/games global update
→ if GameId belongs to active set
→ gamefull
```

Ancak:

```text
"same ch = exact reason"
```

henüz PROVEN değil.

---

# 34. CURRENT COLLECTOR VS BROWSER

Current collector:

```text
prematch/games
→ getprematchgameall
→ partial merge
→ pc check
→ periodic gamefull reconciliation
```

Observed browser:

```text
prematch/games
→ relevant GameId?
→ getprematchgamefull
→ authoritative refresh
```

Bu fark çok önemli.

Collector architecture muhtemelen değişecek.

AMA HENÜZ DEĞİŞTİRME.

Önce relevance selection logic hakkında bir temiz deney daha yap.

---

# 35. DO NOT YET

Henüz:

```text
inspect_prematch_games.py production refactor
DB
Redis
Kafka/RabbitMQ
Java rewrite
reconciliation policy rewrite
```

yapma.

Önce frontend relevant-set behavior’ını biraz daha kanıtla.

---

# 36. EXACT NEXT EXPERIMENT — THIS IS THE NEXT STEP

Yeni chat’te ilk görev BU.

MyStake’ta **tek bir league / competition page** açılacak.

Mümkünse o competition içinde:

```text
5–20 prematch matches
```

olsun.

Test sırasında:

```text
tek competition açık
başka prematch league/tab açma
Network Preserve Log ON
Network clear
5–10 dakika observe
HAR export
```

Kullanıcı önce competition/league URL’sini gönderecek.

İlk yapacağın şey:

```text
URL'yi incele
→ clean test setup ver
```

Sonra HAR alınacak.

HAR’dan çıkarılacaklar:

```text
1. gamefull çekilen tüm GameId'ler
2. her GameId'nin sport değeri
3. her GameId'nin ch değeri
4. açık competition'ın ch değeri
5. kaç gamefull GameId aynı ch'ye ait
6. farklı ch'ye ait olanlar neden fetch edilmiş olabilir
7. timing:
   prematch/games
   → gamefull latency
```

Goal:

```text
Does frontend's relevant fixture set
correspond primarily to the currently open competition?
```

Eğer yüksek oranda aynı `ch` çıkarsa:

```text
prematch/games
→ frontend active competition filter
→ gamefull relevant fixtures
```

modeli ciddi şekilde güçlenecek.

---

# 37. WHAT WE SHOULD NOT CLAIM YET

Şunları henüz kesin söyleme:

```text
gamefull always follows every prematch/games entry
```

Yanlış.

Global stream çok büyük; only relevant subset fetch ediliyor.

Şunu da kesin söyleme:

```text
relevant = same ch
```

Henüz hypothesis.

Şunu da kesin söyleme:

```text
UpdateTimeStamp == game.up
```

Çok güçlü relation var ama exact semantics henüz proven değil.

---

# 38. CURRENT PROVEN FACTS SUMMARY

```text
MQTT connection                         ✅
reconnect/resubscribe                   ✅
unsubscribe                             ✅
cache indirection                       ✅
live exact topic                        ✅
live diff                               ✅
match end                               ✅

prematch/header                         ✅
prematch/games                          ✅
prematch/markets                        ✅

gameall endpoint                        ✅
gamefull endpoint                       ✅

gameall partial                         ✅
gameall complete logical delta          ❌ disproven
missing gameall market = unchanged      ❌ disproven
pc match = exact state                  ❌ disproven

gamefull authoritative                  ✅

prematch/games UpdateList
→ browser gamefull correlation          ✅ strong proof

76433701 correlation                    7/7

global same-GameId ≤2 sec correlation   164/172 ≈95.3%

28/28 gamefull GameIds have preceding
same-GameId prematch/games signal       ✅

HAR realtime gameall calls              0
HAR gamefull calls                      172

prematch/games likely invalidation /
refresh signal                          ✅ very strong evidence

frontend relevant fixture set           ✅ strong evidence

exact relevance selection logic         ⏳ NEXT

same ch/open competition relation        ⏳ NEXT EXPERIMENT
```

---

# 39. NEXT CHAT OPENING PROMPT

Yeni chat’e bu context’in tamamını koyduktan sonra en alta şunu yaz:

```text
Devam edelim.

Bir sonraki görevimiz relevant fixture set hipotezini test etmek.

MyStake'ta tek bir league/competition sayfası açacağım.
Mümkünse 5–20 prematch fixture olacak.

Sana league/competition URL'sini göndereceğim.

Sonra:
- Chrome Network temizlenecek
- Preserve Log açık olacak
- 5–10 dakika traffic toplanacak
- HAR export edilecek

HAR'dan:
- gamefull GameId'leri
- sport
- ch
- açık competition ch
- prematch/games → gamefull correlation
çıkaracağız.

Amacımız browser'ın relevant fixture set'inin açık competition ile ilişkisini kanıtlamak.

Henüz collector code architecture'ını değiştirmiyoruz.

Bana tek adım ver, ben sonucu göndereyim.
```

---

# 40. MOST IMPORTANT REMINDER

Şu an elimizdeki en önemli mimari bulgu:

```text
OLD ASSUMPTION

prematch/games
→ gameall delta
→ merge
→ occasional full reconciliation
```

yerine browser gerçek davranışı şu yönde:

```text
prematch/games
→ relevant GameId
→ gamefull
→ authoritative refresh
```

görünüyor.

Bu yüzden artık `gameall` merge logic’i üzerinde daha fazla tweak yaparak zaman kaybetme.

Önce relevant fixture selection behavior’ını çöz.

Sonra collector architecture’ını yeniden değerlendir.
