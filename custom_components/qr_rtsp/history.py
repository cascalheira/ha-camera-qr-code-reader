"""Persisted scan history (an audit log of QR code usage)."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import DOMAIN

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.history"
MAX_EVENTS = 200  # per config entry
SAVE_DELAY = 10  # seconds, debounced


class ScanHistory:
    """In-memory ring buffer of scan events, debounce-persisted to .storage."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the store."""
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict[str, list[dict[str, Any]]] = {}

    async def async_load(self) -> None:
        """Load persisted history."""
        stored = await self._store.async_load()
        if stored:
            self._data = {k: list(v) for k, v in stored.items()}

    @callback
    def add(self, entry_id: str, event: dict[str, Any]) -> None:
        """Record a scan event (newest first), trimmed to MAX_EVENTS."""
        events = self._data.setdefault(entry_id, [])
        events.insert(0, event)
        del events[MAX_EVENTS:]
        self._store.async_delay_save(self._data_to_save, SAVE_DELAY)

    @callback
    def get(self, entry_id: str) -> list[dict[str, Any]]:
        """Return a copy of an entry's events (newest first)."""
        return list(self._data.get(entry_id, []))

    @callback
    def clear(self, entry_id: str) -> None:
        """Clear an entry's history."""
        self._data[entry_id] = []
        self._store.async_delay_save(self._data_to_save, SAVE_DELAY)

    @callback
    def _data_to_save(self) -> dict[str, list[dict[str, Any]]]:
        return self._data
