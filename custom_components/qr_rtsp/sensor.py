"""Sensor exposing the most recently scanned QR code."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import QrRtspConfigEntry
from .const import CONF_NAME, DEFAULT_NAME, DOMAIN, SIGNAL_UPDATE

# HA sensor states are capped at 255 chars; longer payloads live in attributes.
_MAX_STATE_LEN = 255


async def async_setup_entry(
    hass: HomeAssistant,
    entry: QrRtspConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the last-scanned sensor."""
    async_add_entities([LastQrSensor(entry)])


class LastQrSensor(SensorEntity):
    """Reports the last decoded QR payload, with details in attributes."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:qrcode-scan"
    _attr_should_poll = False

    def __init__(self, entry: QrRtspConfigEntry) -> None:
        """Initialize the sensor."""
        self._entry = entry
        name = {**entry.data, **entry.options}.get(CONF_NAME, DEFAULT_NAME)
        self._attr_name = "Last QR code"
        self._attr_unique_id = f"{entry.entry_id}_last_qr"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=name,
            manufacturer="QR Code RTSP Reader",
        )

    @property
    def native_value(self) -> str | None:
        """Return the last payload, truncated to the state length limit."""
        payload = self._entry.runtime_data.last_payload
        if payload is None:
            return None
        return payload[:_MAX_STATE_LEN]

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Return full payload and metadata."""
        data = self._entry.runtime_data
        return {
            "payload": data.last_payload,
            "type": data.last_type,
            "scanned_at": data.last_scanned,
            "authorized": data.last_authorized,
            "reason": data.last_reason,
            "rule": data.last_rule,
        }

    async def async_added_to_hass(self) -> None:
        """Subscribe to scan updates."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_UPDATE.format(entry_id=self._entry.entry_id),
                self._handle_update,
            )
        )

    @callback
    def _handle_update(self) -> None:
        """Refresh state when a new code is scanned."""
        self.async_write_ha_state()
