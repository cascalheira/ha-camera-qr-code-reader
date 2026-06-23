"""The QR Code RTSP Reader integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import (
    CONF_COOLDOWN,
    CONF_DEFAULT_ALLOW_UNLISTED,
    CONF_FPS,
    CONF_NAME,
    CONF_RTSP_TRANSPORT,
    CONF_RULES,
    CONF_STREAM_URL,
    CONF_WIDTH,
    DEFAULT_ALLOW_UNLISTED,
    DEFAULT_COOLDOWN,
    DEFAULT_FPS,
    DEFAULT_NAME,
    DEFAULT_TRANSPORT,
    DEFAULT_WIDTH,
    DOMAIN,
    EVENT_QR_SCANNED,
    REASON_NO_RULES,
    REASON_OK,
    REASON_UNKNOWN,
    RULE_NAME,
    RULE_PAYLOAD,
    RULE_SCRIPT,
    SIGNAL_UPDATE,
)
from .panel import async_register_panel, async_remove_panel
from .rules import evaluate, find_rule
from .scanner import QrStreamScanner
from .services import async_setup_services, async_unload_services
from .websocket_api import async_register_websocket_api

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]


@dataclass
class QrRtspData:
    """Runtime data for a config entry."""

    scanner: QrStreamScanner | None = None
    scanner_signature: tuple | None = None
    last_payload: str | None = None
    last_type: str | None = None
    last_scanned: datetime | None = None
    last_authorized: bool | None = None
    last_reason: str | None = None
    last_rule: str | None = None


type QrRtspConfigEntry = ConfigEntry[QrRtspData]


def _scanner_signature(entry: QrRtspConfigEntry) -> tuple:
    """Options that, when changed, require restarting the ffmpeg stream."""
    merged = {**entry.data, **entry.options}
    return (
        merged.get(CONF_STREAM_URL),
        merged.get(CONF_RTSP_TRANSPORT, DEFAULT_TRANSPORT),
        merged.get(CONF_FPS, DEFAULT_FPS),
        merged.get(CONF_WIDTH, DEFAULT_WIDTH),
        merged.get(CONF_COOLDOWN, DEFAULT_COOLDOWN),
    )


async def async_setup_entry(hass: HomeAssistant, entry: QrRtspConfigEntry) -> bool:
    """Set up QR Code RTSP Reader from a config entry."""
    options = {**entry.data, **entry.options}
    name = options.get(CONF_NAME, DEFAULT_NAME)

    data = QrRtspData(scanner_signature=_scanner_signature(entry))
    entry.runtime_data = data

    @callback
    def _handle_scan(payload: str, symbol_type: str) -> None:
        # Read rules live so admin edits take effect without a stream restart.
        rules = entry.options.get(CONF_RULES, [])
        allow_unlisted = entry.options.get(
            CONF_DEFAULT_ALLOW_UNLISTED, DEFAULT_ALLOW_UNLISTED
        )

        rule = find_rule(rules, payload)
        if rule is not None:
            authorized, reason = evaluate(rule, dt_util.now())
            rule_name = rule.get(RULE_NAME) or rule.get(RULE_PAYLOAD)
        elif rules:
            authorized = allow_unlisted
            reason = REASON_OK if allow_unlisted else REASON_UNKNOWN
            rule_name = None
        else:
            authorized, reason, rule_name = True, REASON_NO_RULES, None

        data.last_payload = payload
        data.last_type = symbol_type
        data.last_scanned = dt_util.utcnow()
        data.last_authorized = authorized
        data.last_reason = reason
        data.last_rule = rule_name

        hass.bus.async_fire(
            EVENT_QR_SCANNED,
            {
                "entry_id": entry.entry_id,
                "name": name,
                "payload": payload,
                "type": symbol_type,
                "authorized": authorized,
                "reason": reason,
                "rule": rule_name,
            },
        )
        async_dispatcher_send(hass, SIGNAL_UPDATE.format(entry_id=entry.entry_id))

        # Run the rule's configured script on an authorized scan.
        if authorized and rule is not None and rule.get(RULE_SCRIPT):
            hass.async_create_task(
                hass.services.async_call(
                    "script",
                    "turn_on",
                    {"entity_id": rule[RULE_SCRIPT]},
                    blocking=False,
                ),
                name=f"qr_rtsp run {rule[RULE_SCRIPT]}",
            )

    data.scanner = QrStreamScanner(
        hass,
        get_ffmpeg_manager(hass).binary,
        options[CONF_STREAM_URL],
        fps=float(options.get(CONF_FPS, DEFAULT_FPS)),
        width=int(options.get(CONF_WIDTH, DEFAULT_WIDTH)),
        cooldown=float(options.get(CONF_COOLDOWN, DEFAULT_COOLDOWN)),
        transport=options.get(CONF_RTSP_TRANSPORT, DEFAULT_TRANSPORT),
        on_scan=_handle_scan,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await data.scanner.async_start()

    async_setup_services(hass)
    async_register_websocket_api(hass)
    await async_register_panel(hass)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: QrRtspConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok and entry.runtime_data.scanner:
        await entry.runtime_data.scanner.async_stop()

    remaining = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.entry_id != entry.entry_id
    ]
    if unload_ok and not remaining:
        async_unload_services(hass)
        async_remove_panel(hass)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: QrRtspConfigEntry) -> None:
    """Reload only when stream settings change; rule edits apply live."""
    new_signature = _scanner_signature(entry)
    if entry.runtime_data.scanner_signature != new_signature:
        await hass.config_entries.async_reload(entry.entry_id)
