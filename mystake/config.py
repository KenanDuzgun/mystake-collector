MQTT_WEBSOCKET_URL = "wss://wss-eu-uk1.ws-amazon.com/mqtt"
MQTT_WEBSOCKET_SUBPROTOCOL = "mqtt"

MQTT_PROTOCOL_NAME = "MQTT"
MQTT_PROTOCOL_LEVEL = 4

MQTT_KEEP_ALIVE_SECONDS = 60
MQTT_CLEAN_SESSION = True

MQTT_TOPIC_PREMATCH_HEADER = "prematch/header"

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
