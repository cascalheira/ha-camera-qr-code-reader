"""Constants for the QR Code RTSP Reader integration."""

from __future__ import annotations

DOMAIN = "qr_rtsp"

# Config / option keys
CONF_NAME = "name"
CONF_STREAM_URL = "stream_url"
CONF_RTSP_TRANSPORT = "rtsp_transport"
CONF_FPS = "fps"
CONF_WIDTH = "width"
CONF_COOLDOWN = "cooldown"

# Defaults
DEFAULT_NAME = "QR Reader"
DEFAULT_TRANSPORT = "tcp"
DEFAULT_FPS = 4.0
DEFAULT_WIDTH = 640  # downscale width in px; 0 = no scaling
DEFAULT_COOLDOWN = 3.0  # seconds before the same payload fires again

TRANSPORTS = ["tcp", "udp"]

# Processing mode: scan on this HA host, or offload to the remote Rust service.
CONF_PROCESSING_MODE = "processing_mode"
CONF_SERVICE_URL = "service_url"
CONF_SECRET_KEY = "secret_key"
MODE_LOCAL = "local"
MODE_REMOTE = "remote"
DEFAULT_MODE = MODE_LOCAL
PROCESSING_MODES = [MODE_LOCAL, MODE_REMOTE]

# Access-rule storage (lives in entry.options)
CONF_RULES = "rules"
CONF_DEFAULT_ALLOW_UNLISTED = "default_allow_unlisted"
DEFAULT_ALLOW_UNLISTED = True

# Per-rule fields
RULE_NAME = "name"  # short identifier, embedded in the payload
RULE_TITLE = "title"  # free-form description shown in the UI (not in payload)
RULE_PAYLOAD = "payload"
RULE_VALID_FROM = "valid_from"  # ISO date, inclusive
RULE_VALID_UNTIL = "valid_until"  # ISO date, inclusive (whole day)
RULE_WEEKDAYS = "weekdays"  # list of WEEKDAYS values; empty = all days
RULE_START_TIME = "start_time"  # ISO time, inclusive
RULE_END_TIME = "end_time"  # ISO time, inclusive (may wrap past midnight)
RULE_SCRIPT = "script_entity"  # script.* entity to run on an authorized scan

# Monday-first weekday keys, aligned with datetime.weekday()
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# Authorization reasons reported on the event/sensor
REASON_OK = "ok"
REASON_NO_RULES = "no_rules"
REASON_UNKNOWN = "unknown_code"
REASON_NOT_YET = "not_yet_valid"
REASON_EXPIRED = "expired"
REASON_OUT_OF_SCHEDULE = "out_of_schedule"

# Generated payloads are "PAYLOAD_PREFIX|<name>|<random>".
# Kept short on purpose so the QR stays sparse (easier to scan on cheap cameras).
PAYLOAD_PREFIX = "hcqrcr"
PAYLOAD_SEPARATOR = "|"

# Code-generation service
SERVICE_GENERATE = "generate_code"
DEFAULT_ENTROPY_BYTES = 16  # 128 bits of randomness
MIN_ENTROPY_BYTES = 8
MAX_ENTROPY_BYTES = 64

# Admin panel + static frontend
PANEL_URL_PATH = "qr-rtsp"
PANEL_TITLE = "QR Codes"
PANEL_ICON = "mdi:qrcode-scan"
PANEL_WEBCOMPONENT = "qr-rtsp-panel"
STATIC_URL = "/qr_rtsp_frontend"
PANEL_JS = "qr-rtsp-panel.js"

# Event fired on every (debounced) scan
EVENT_QR_SCANNED = f"{DOMAIN}_scanned"

# Dispatcher signal template (per config entry) used to refresh the sensor
SIGNAL_UPDATE = DOMAIN + "_{entry_id}_update"
