"""WebSocket API powering the admin panel."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import (
    CONF_NAME,
    CONF_RULES,
    DEFAULT_ENTROPY_BYTES,
    DOMAIN,
    MAX_ENTROPY_BYTES,
    MIN_ENTROPY_BYTES,
    RULE_NAME,
    RULE_PAYLOAD,
    RULE_TITLE,
)
from .rules import find_rule, normalize_rule
from .services import async_create_code, async_render_png


@callback
def async_register_websocket_api(hass: HomeAssistant) -> None:
    """Register WebSocket commands (idempotent across entries)."""
    data = hass.data.setdefault(DOMAIN, {})
    if data.get("ws_registered"):
        return
    websocket_api.async_register_command(hass, ws_entries)
    websocket_api.async_register_command(hass, ws_list_rules)
    websocket_api.async_register_command(hass, ws_save_rule)
    websocket_api.async_register_command(hass, ws_delete_rule)
    websocket_api.async_register_command(hass, ws_generate)
    websocket_api.async_register_command(hass, ws_image)
    websocket_api.async_register_command(hass, ws_status)
    websocket_api.async_register_command(hass, ws_history)
    websocket_api.async_register_command(hass, ws_history_clear)
    data["ws_registered"] = True


def _history(hass: HomeAssistant):
    """Return the shared scan-history store, if loaded."""
    return hass.data.get(DOMAIN, {}).get("history")


def _get_entry(hass: HomeAssistant, entry_id: str):
    """Return the config entry if it belongs to this integration."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        return None
    return entry


def _store_rules(hass: HomeAssistant, entry, rules: list[dict[str, Any]]) -> None:
    """Persist the rules list onto the entry's options."""
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_RULES: rules}
    )


def _upsert(rules: list[dict[str, Any]], rule: dict[str, Any], replace: str) -> list:
    """Return rules with `rule` added, dropping any with payload == replace/new."""
    keys = {replace, rule[RULE_PAYLOAD]}
    out = [r for r in rules if r.get(RULE_PAYLOAD) not in keys]
    out.append(rule)
    return out


@websocket_api.require_admin
@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/entries"})
@callback
def ws_entries(hass, connection, msg) -> None:
    """List configured QR reader entries."""
    result = [
        {"entry_id": e.entry_id, "name": e.data.get(CONF_NAME) or e.title}
        for e in hass.config_entries.async_entries(DOMAIN)
    ]
    connection.send_result(msg["id"], {"entries": result})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/rules/list",
        vol.Required("entry_id"): str,
    }
)
@callback
def ws_list_rules(hass, connection, msg) -> None:
    """List the rules of an entry."""
    entry = _get_entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Unknown entry")
        return
    connection.send_result(
        msg["id"], {"rules": list(entry.options.get(CONF_RULES, []))}
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/rules/save",
        vol.Required("entry_id"): str,
        vol.Required("rule"): dict,
        vol.Optional("original_payload"): str,
    }
)
@callback
def ws_save_rule(hass, connection, msg) -> None:
    """Create or update a rule (matched by payload)."""
    entry = _get_entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Unknown entry")
        return
    rule = normalize_rule(msg["rule"])
    if not rule[RULE_PAYLOAD]:
        connection.send_error(msg["id"], "invalid_format", "Payload is required")
        return
    rules = _upsert(
        list(entry.options.get(CONF_RULES, [])),
        rule,
        msg.get("original_payload", rule[RULE_PAYLOAD]),
    )
    _store_rules(hass, entry, rules)
    connection.send_result(msg["id"], {"rules": rules})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/rules/delete",
        vol.Required("entry_id"): str,
        vol.Required("payload"): str,
    }
)
@callback
def ws_delete_rule(hass, connection, msg) -> None:
    """Delete a rule by payload."""
    entry = _get_entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Unknown entry")
        return
    rules = [
        r
        for r in entry.options.get(CONF_RULES, [])
        if r.get(RULE_PAYLOAD) != msg["payload"]
    ]
    _store_rules(hass, entry, rules)
    connection.send_result(msg["id"], {"rules": rules})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/generate",
        vol.Required("entry_id"): str,
        vol.Required("name"): str,
        vol.Optional("entropy_bytes", default=DEFAULT_ENTROPY_BYTES): vol.All(
            int, vol.Range(min=MIN_ENTROPY_BYTES, max=MAX_ENTROPY_BYTES)
        ),
        vol.Optional("rule", default=dict): dict,
    }
)
@websocket_api.async_response
async def ws_generate(hass, connection, msg) -> None:
    """Generate a secure code, render its PNG, and register it as a rule."""
    entry = _get_entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Unknown entry")
        return
    caption = (msg.get("rule") or {}).get(RULE_TITLE) or msg["name"]
    try:
        result = await async_create_code(
            hass, msg["name"], msg["entropy_bytes"], caption=caption
        )
    except ValueError as err:
        connection.send_error(msg["id"], "invalid_format", str(err))
        return

    rule = normalize_rule(
        {
            **msg.get("rule", {}),
            RULE_PAYLOAD: result["payload"],
            RULE_NAME: msg["name"],
        }
    )
    rules = _upsert(list(entry.options.get(CONF_RULES, [])), rule, rule[RULE_PAYLOAD])
    _store_rules(hass, entry, rules)

    connection.send_result(msg["id"], {**result, "rules": rules})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/image",
        vol.Required("entry_id"): str,
        vol.Required("payload"): str,
    }
)
@websocket_api.async_response
async def ws_image(hass, connection, msg) -> None:
    """Return a base64 PNG for an existing (known) code."""
    entry = _get_entry(hass, msg["entry_id"])
    if entry is None:
        connection.send_error(msg["id"], "not_found", "Unknown entry")
        return
    rule = find_rule(entry.options.get(CONF_RULES, []), msg["payload"])
    if rule is None:
        connection.send_error(msg["id"], "not_found", "Unknown code")
        return
    caption = rule.get(RULE_TITLE) or rule.get(RULE_NAME) or msg["payload"]
    image_b64 = await async_render_png(hass, msg["payload"], caption)
    connection.send_result(msg["id"], {"image_b64": image_b64})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/status",
        vol.Required("entry_id"): str,
    }
)
@callback
def ws_status(hass, connection, msg) -> None:
    """Return the live scanner status for an entry."""
    entry = _get_entry(hass, msg["entry_id"])
    scanner = getattr(getattr(entry, "runtime_data", None), "scanner", None)
    if scanner is None:
        connection.send_result(
            msg["id"], {"state": "stopped", "connected": False}
        )
        return
    connection.send_result(msg["id"], scanner.status)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/history/list",
        vol.Required("entry_id"): str,
    }
)
@callback
def ws_history(hass, connection, msg) -> None:
    """Return the scan history for an entry (newest first)."""
    history = _history(hass)
    events = history.get(msg["entry_id"]) if history else []
    connection.send_result(msg["id"], {"events": events})


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/history/clear",
        vol.Required("entry_id"): str,
    }
)
@callback
def ws_history_clear(hass, connection, msg) -> None:
    """Clear the scan history for an entry."""
    history = _history(hass)
    if history:
        history.clear(msg["entry_id"])
    connection.send_result(msg["id"], {"events": []})
