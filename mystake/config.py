MQTT_WEBSOCKET_URL = "wss://wss-eu-uk1.ws-amazon.com/mqtt"
MQTT_WEBSOCKET_SUBPROTOCOL = "mqtt"

MQTT_PROTOCOL_NAME = "MQTT"
MQTT_PROTOCOL_LEVEL = 4

MQTT_KEEP_ALIVE_SECONDS = 60
MQTT_CLEAN_SESSION = True

MQTT_TOPIC_PREMATCH_HEADER = "prematch/header"

# PROVEN topic (docs/handoff/handoff.md section 7): a broad/global
# prematch revalidation signal. Payload semantics (UpdateList/DeleteList
# contents) are not proven - see docs/product/SCHEMA.md section 4.
MQTT_TOPIC_PREMATCH_GAMES = "prematch/games"

# PROVEN: GET https://analytics-sp.googleserv.tech/api/sport/getheader/en
# returns the prematch fixture discovery hierarchy directly as JSON
# (see docs/product/SCHEMA.md).
PREMATCH_API_BASE_URL = "https://analytics-sp.googleserv.tech"
PREMATCH_GETHEADER_PATH = "/api/sport/getheader/en"
PREMATCH_GETHEADER_URL = f"{PREMATCH_API_BASE_URL}{PREMATCH_GETHEADER_PATH}"

# STRONG EVIDENCE (by analogy, not directly observed for this key):
# other cache-indirected resources (e.g. `prematch/games`) are fetched
# via `{CACHE_GET_BASE_URL}?key=<cache-key>` (docs/handoff/handoff.md
# section 7). `live/headernew/en` is assumed to follow the same
# pattern since it is documented as "the observed cache resource" for
# live fixture discovery.
CACHE_GET_BASE_URL = "https://wss-eu-uk1.ws-amazon.com/api/cache/get"
LIVE_HEADER_CACHE_KEY = "live/headernew/en"

# PROVEN endpoint (docs/product/SCHEMA.md section 2):
# GET /api/prematch/getprematchgamefull/{PREMATCH_CONTEXT_ID}/{GameId}
# `28` itself remains UNKNOWN semantics (confirmed NOT a sport id).
PREMATCH_CONTEXT_ID = 28
PREMATCH_GETPREMATCHGAMEFULL_URL_TEMPLATE = (
    f"{PREMATCH_API_BASE_URL}/api/prematch/getprematchgamefull/"
    f"{PREMATCH_CONTEXT_ID}/{{game_id}}"
)

# Phase 3 tracked-game hydration bounds (AGENTS.md section 4:
# conservative rate limiting; section 3: no unbounded fetch fan-out).
# These are conservative defaults, not observed platform limits.
PREMATCH_TRACKED_GAMES_MAX = 5
PREMATCH_HYDRATION_MAX_CONCURRENCY = 2
PREMATCH_HYDRATION_MIN_REQUEST_INTERVAL_SECONDS = 0.5

# PROVEN topic template (docs/product/SCHEMA.md section 3): exact,
# per-GameId live subscription. The wildcard form `live/gamenew/#` was
# observed to be rejected with SUBACK 0x80 - never subscribe to it.
MQTT_TOPIC_LIVE_GAME_PREFIX = "live/gamenew/"

# Phase 4B bounded multi-game live tracking (AGENTS.md section 4: no
# unbounded fan-out). A conservative default, not an observed platform
# limit.
LIVE_TRACKED_GAMES_MAX = 5
