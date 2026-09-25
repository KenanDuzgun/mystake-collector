# MyStake Collector — New Chat Handoff (25 September 2026)

## 0. Yeni chat'e başlangıç talimatı

Bu doküman, yeni ChatGPT oturumunda `mystake-collector` projesini **aynı yerden devam ettirmek** için hazırlanmıştır. Kullanıcı tekrar projeyi açıklamak zorunda kalmamalı. Türkçe, kısa ve sonuç odaklı yanıt ver. Her aşamada kesin olarak söyle: **neyi alabiliyoruz, ne eksik, sonraki tek görev ne**. Tekrar eden HAR/deney döngüsüne dönme. Önce mevcut kodu ve test sonuçlarını gör, sonra eksikleri hedefli kapat. Yeni veri istemeyi yalnızca somut bir engel varsa öner.

**Anlık görev:** Phase 2 Final Verification & Schema Completion. Claude Code'a verilecek hazır prompt ayrıca `MyStake_Phase2_Final_Verification_Claude_Prompt.md` dosyasında. Kullanıcı prompt'u yeni Claude Code context'ine yapıştıracak. Sonucu buraya getirince gerçek sonuçları değerlendir ve ancak sonrasında Phase 3'e geç.

## 1. Amaç / ortam

Tarayıcı olmadan MyStake'ın **tüm desteklenen sporları** için prematch ve live fixture discovery, market/selection/odds güncellemeleri, score, match-end, final state ve cleanup sağlayan Python collector.

Mac / PyCharm / Python 3.13 / uv; proje: `~/Desktop/Projects/mystake-collector`; test: `uv run pytest`. Katman kuralı: `sources/` transport, `pipeline/` decode/diff, `models/` typed data, `registry/` fixture state, `events/` semantic events. `AGENTS.md` authoritative. Sıra: observe -> experiment -> prove -> productionize, ancak artık protokolün temel kısmı yeterince anlaşıldığı için **uygulama ve kabul testleri öncelikli**. Şimdilik Java/DB/Redis/Kafka/RabbitMQ yok.

## 2. Önemli düzeltme: dosyalar

- Orijinal ZIP daha eski, Phase 1 öncesi koddur; yeni Claude Code değişiklikleri kullanıcının Mac'indeki **güncel çalışma ağacında**, henüz yeni ZIP gönderilmedi. Eski ZIP'i güncel implementasyon sanma.
- ZIP'teki `main.py` tek live GameId denemesiydi. Uzun prematch/gameall merge deney kodu `inspect_prematch_games.py` içindeydi (önceki yanıtta bu isim karışmıştı).
- `tests/fixtures/mystake-live-header-sanitized.json` **doğru fixture dosya adı**. Önceki prompt'lardaki `tests/fixtures/live_header.json` yanlıştı. Kullanıcı dosyanın `fixtures` altında olduğunu söyledi. Dosyayı yeniden adlandırma/yeniden üretme/üzerine yazma.
- HAR'lar: `1.har` ana sayfa, `2.har` Sports prematch, `3.har` Upcoming, `4.har` live eventview `76509222`. `4.har`, `3.har` kayıtlarının bir bölümünü içerir; bağımsız gözlem diye çift sayma.

## 3. Kaynaklar ve kesin/kuvvetli bulgular

**Prematch discovery:** `GET https://analytics-sp.googleserv.tech/api/sport/getheader/en`, hierarchy `Sports -> Regions -> Champs -> GameSmallItems`, item `ID` GameId olarak alınır. Tarihi capture yaklaşık 4.086 fixture / 42 sport; güncel sayı değildir. `prematch/header` MQTT sinyali ardından browser `getheader/en` tekrar çağırdı (farklı oturumlarda ~258–259 ms). Bu bir registry revalidation/invalidation sinyali; mesaj gelmesi değişiklik garanti etmez. `prematch/games` geniş/global *revalidation* akışıdır; complete fixture discovery DEĞİL. Bildirim gelince ilgili/tracked GameId için `getprematchgamefull/28/{GameId}` authoritative snapshot alınmalı. Her notification -> her GameId -> fetch değil; dedupe/coalesce ve bounded requests gerekir.

**Prematch data:** `getprematchgameall/{language}/28/?games=,...` partial/list/bootstrap representation. Complete logical delta **DEĞİL**. Eski `gameall -> partial merge -> pc check -> every-10 gamefull` yaklaşımı güvenilir değildi; soak testte 11 reconciliation / 10 repair. `pc` eşitliği exact-state correctness garanti etmez. `gamefull` daha güvenilir full state; yeni production akışı önce full, güncellemede full replace ve diff. `28`, `ch`, `prematch/markets` ve `DeleteList` semantiği bilinmiyor; uydurma. `DeleteList` kalıcı fixture deletion diye uygulanmamalı.

**Live discovery:** Cache key `live/headernew/en`, raw payload: `Games`, `Sports`, `Regions`, `Championats`, `Teams`, `mk`. Exact live topic `live/gamenew/{GameId}` çalışıyor; `live/gamenew/#` wildcard `SUBACK 0x80` ile reddedilmiş. Live payload/cache flow: MQTT -> cache URL -> HTTP GET -> base64 -> optional gzip -> JSON. Live snapshot/diff ve match-end için mevcut kod var. Match-end gözlemlenen football triple `Status==3, BetStatus==0, EventStatus==40`; bunu sadece üç alan tek diff'te değiştiğinde tetikleyen bug Phase1'de rapora göre düzeltildi. Başka sporların status code'larına aynı semantiği taşımamak gerek.

**Prematch -> live:** Aynı GameId mi, hangi geçiş olayı var, kickoff/removal zamanlaması henüz kanıtlanmış değil. Bu Phase2 scope dışı ve ileride hedefli ele alınacak.

## 4. Gerçek live fixture JSON (kritik)

Dosya: `tests/fixtures/mystake-live-header-sanitized.json`. 24 Eylül 2026 capture; gerçekten kontrol edilen root/list sizes: `Games=84`, `Sports=15`, `Regions=37`, `Championats=52`, `Teams=167`, `mk=[]`. Bu sayılar *test fixture* sayılarıdır, current network expectation değil.

Referanslar: `Games[].ID` GameId; `.Sport -> Sports[].ID`, `.Region -> Regions[].ID`, `.Champ -> Championats[].ID`, `.Team1/.Team2 -> Teams[].ID`; lookup kayıtlarında `ID`, `Name` alanları var. Capture'daki 84 Games için lookup referansları mevcut. Game `76509222`: Sport `1` Soccer, Region `48` Chile, Champ `106505` Copa Chile, Knockout stage, Team1 `10963` CD Everton Vina del Mar, Team2 `10975` Universidad de Chile; capture score 0:0 (güncel skor değil). `mk` boş olduğundan schema UNKNOWN; `MatchStatusID`, `ls`, `bgid` gibi kodları tahmin etme.

Önceki Claude raporunda 'real live payload unavailable' denildiği için live parser sadece `Games[]` üzerinden ID çıkarıyor, isim lookup'ları eksik kalmıştı. Bu fixture ile parser ve testler tamamlanacak.

## 5. Fazlar ve raporlanan test sonuçları

**Phase 1 — Foundation:** kullanıcı tarafından aktarılan Claude/Codex raporuna göre SUBACK beklerken gelen PUBLISH kaybı düzeltildi; match-end detection düzeltildi; typed models ve `docs/product/SCHEMA.md` eklendi; 47 baseline + 22 new = **69 passed**. Bunlar geçmiş ajanın raporudur; güncel repo bağımsız tekrar test edilmedi.

**Phase 2 — Fixture Discovery:** kullanıcı tarafından aktarılan ajan raporuna göre `prematch_header_parser`, `live_header_parser`, discovery services, `FixtureRegistry`, HTTP/cache client integration, MQTT prematch header refresh handler, `discover_fixtures.py` ve testler eklendi; **111 passed** raporlandı. Fakat **live join mapping eksik** ve **real network integration hiç çalıştırılmadı**. Dolayısıyla Phase2 unit-test implementation mevcut, fakat Phase2 tam doğrulanmış değil. Header refresh handler'ın gerçek executable path'e bağlanıp bağlanmadığı da kontrol edilmeli; önceki ajan 'subscribes conceptually' demişti.

**Phase 2 Final Verification (şu an):** doğru fixture dosyasına göre names lookup, regression tests, registry state preservation, actual `uv run python discover_fixtures.py`, current prematch/live counts ve sample, MQTT wiring check, docs update. Network erişimi yoksa açıkça `NOT VERIFIED` yaz ve Mac komutunu ver; mock/fixture ile live verification yapılmış deme. Phase3'e otomatik geçme.

## 6. Sonraki aşamalar — ama ŞİMDİ implement etme

- Phase 3: Registry'de tracked GameIds için rate-limited/bounded full prematch market hydration ve updates; `gamefull` replace/diff, stale/error handling; `gameall` merge kullanılmaz; binlerce maça kör fetch yok.
- Phase 4: Live registry -> exact GameId subscription, multi-game tracking, reconnect/resubscribe, snapshot/diff, match-end finalization.
- Later: prematch->live transition proof/wiring, persistence/coverage/acceptance tests. Raw field retention; norm schema mapping only with evidence.

## 7. Yeni ChatGPT asistanından beklenen davranış

Kullanıcı Claude çıktısını getirince önce eksik bilgi istemeden raporu incele: gerçek CLI çalışmış mı, status/counts/sample var mı, testler, header refresh gerçekten wired mı, missing lookup doğru, raw korunmuş mu. Gerekirse güncel kaynak ZIP istenebilir ama gereksiz soru veya yeni HAR isteğiyle başlamayın. 'Phase 2 complete' demek için unit tests + gerçek entegrasyon ayrımını koru. Sonraki görevi tek net prompt olarak ver. Kullanıcı ayrıntıyı anlamadığını söyledi: kısa Türkçe yönetici özetiyle başla; teknik ayrıntı ve tam Claude prompt'u altına ver.
