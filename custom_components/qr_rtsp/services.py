"""The `generate_code` service: mint secure QR codes and optional rules."""

from __future__ import annotations

import base64
import logging
import secrets
from io import BytesIO
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_RULES,
    DEFAULT_ENTROPY_BYTES,
    DOMAIN,
    MAX_ENTROPY_BYTES,
    MIN_ENTROPY_BYTES,
    PAYLOAD_PREFIX,
    PAYLOAD_SEPARATOR,
    RULE_END_TIME,
    RULE_NAME,
    RULE_PAYLOAD,
    RULE_SCRIPT,
    RULE_START_TIME,
    RULE_TITLE,
    RULE_VALID_FROM,
    RULE_VALID_UNTIL,
    RULE_WEEKDAYS,
    SERVICE_GENERATE,
    WEEKDAYS,
)
from .rules import normalize_rule

_LOGGER = logging.getLogger(__name__)

ATTR_NAME = "name"
ATTR_ENTROPY = "entropy_bytes"
ATTR_REGISTER = "register"
ATTR_DEVICE_ID = "device_id"

_GENERATE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_NAME): cv.string,
        vol.Optional(RULE_TITLE): cv.string,
        vol.Optional(ATTR_ENTROPY, default=DEFAULT_ENTROPY_BYTES): vol.All(
            vol.Coerce(int), vol.Range(min=MIN_ENTROPY_BYTES, max=MAX_ENTROPY_BYTES)
        ),
        vol.Optional(ATTR_REGISTER, default=False): cv.boolean,
        vol.Optional(ATTR_DEVICE_ID, default=[]): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(RULE_VALID_FROM): cv.string,
        vol.Optional(RULE_VALID_UNTIL): cv.string,
        vol.Optional(RULE_WEEKDAYS): vol.All(cv.ensure_list, [vol.In(WEEKDAYS)]),
        vol.Optional(RULE_START_TIME): cv.string,
        vol.Optional(RULE_END_TIME): cv.string,
        vol.Optional(RULE_SCRIPT): cv.entity_id,
    }
)


async def async_create_code(
    hass: HomeAssistant, name: str, entropy_bytes: int
) -> dict[str, Any]:
    """Build a secure payload, render its PNG, and return the details.

    Raises ValueError if the name is invalid.
    """
    name = (name or "").strip()
    if not name or PAYLOAD_SEPARATOR in name or "\n" in name:
        raise ValueError(
            f"Name must be non-empty and cannot contain '{PAYLOAD_SEPARATOR}'."
        )

    token = secrets.token_urlsafe(entropy_bytes)
    payload = PAYLOAD_SEPARATOR.join((PAYLOAD_PREFIX, name, token))
    return {
        "payload": payload,
        "name": name,
        "random": token,
        # base64 PNG — delivered only over authenticated channels, never to /www.
        "image_b64": await async_render_png(hass, payload),
    }


async def async_render_png(hass: HomeAssistant, payload: str) -> str:
    """Render a QR code PNG and return it base64-encoded."""
    return await hass.async_add_executor_job(_render_png_b64, payload)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the domain-level generate_code service (once)."""
    if hass.services.has_service(DOMAIN, SERVICE_GENERATE):
        return

    async def _handle_generate(call: ServiceCall) -> ServiceResponse:
        try:
            result = await async_create_code(
                hass, call.data[ATTR_NAME], call.data[ATTR_ENTROPY]
            )
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err

        registered: list[str] = []
        if call.data[ATTR_REGISTER]:
            entries = _entries_for_devices(hass, call.data[ATTR_DEVICE_ID])
            if not entries:
                raise ServiceValidationError(
                    "Enable 'register' with at least one QR Code RTSP Reader device."
                )
            rule = normalize_rule(
                {
                    **call.data,
                    RULE_PAYLOAD: result["payload"],
                    RULE_NAME: result["name"],
                }
            )
            for entry in entries:
                _add_rule(hass, entry, rule)
                registered.append(entry.entry_id)

        _LOGGER.debug("Generated QR for %r (registered: %s)", result["name"], registered)
        return {**result, "registered_entries": registered}

    hass.services.async_register(
        DOMAIN,
        SERVICE_GENERATE,
        _handle_generate,
        schema=_GENERATE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )


@callback
def async_unload_services(hass: HomeAssistant) -> None:
    """Remove the service when the last entry is unloaded."""
    hass.services.async_remove(DOMAIN, SERVICE_GENERATE)


def _render_png_b64(payload: str) -> str:
    """Render the QR code to a base64 PNG string. Runs in the executor."""
    import qrcode  # noqa: PLC0415 - heavy import, deferred to runtime

    buffer = BytesIO()
    qrcode.make(payload).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def _entries_for_devices(
    hass: HomeAssistant, device_ids: list[str]
) -> list[ConfigEntry]:
    """Resolve selected devices to this integration's config entries."""
    registry = dr.async_get(hass)
    entries: dict[str, ConfigEntry] = {}
    for device_id in device_ids:
        device = registry.async_get(device_id)
        if device is None:
            continue
        for entry_id in device.config_entries:
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry and entry.domain == DOMAIN:
                entries[entry_id] = entry
    return list(entries.values())


@callback
def _add_rule(hass: HomeAssistant, entry: ConfigEntry, rule: dict[str, Any]) -> None:
    """Append (replacing by payload) a rule to an entry."""
    rules = [
        r
        for r in entry.options.get(CONF_RULES, [])
        if r.get(RULE_PAYLOAD) != rule[RULE_PAYLOAD]
    ]
    rules.append(rule)
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_RULES: rules}
    )
