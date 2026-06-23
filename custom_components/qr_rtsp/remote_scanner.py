"""Offloaded scanner: receive QR detections from the remote Rust service.

Mirrors QrStreamScanner's interface (async_start/async_stop/status + on_scan
callback) so the rest of the integration is agnostic to where decoding happens.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

_MAX_BACKOFF = 30


class RemoteScanner:
    """Connects to the Rust service over WebSocket and relays scan events."""

    def __init__(
        self,
        hass: HomeAssistant,
        service_url: str,
        secret: str,
        *,
        stream_url: str,
        transport: str,
        fps: float,
        width: int,
        cooldown: float,
        on_scan: Callable[[str, str], None],
    ) -> None:
        """Initialize the remote scanner."""
        self.hass = hass
        self._service_url = service_url
        self._secret = secret
        self._stream_url = stream_url
        self._transport = transport
        self._fps = fps
        self._width = width
        self._cooldown = cooldown
        self._on_scan = on_scan

        self._task: asyncio.Task | None = None
        self._closing = False
        self._last_seen: dict[str, float] = {}

        self._state = "starting"
        self._connected = False
        self._frames = 0
        self._last_frame = None
        self._last_error: str | None = None

    @property
    def status(self) -> dict:
        """Current status for the admin panel."""
        return {
            "state": self._state,
            "connected": self._connected,
            "frames": self._frames,
            "last_frame": self._last_frame.isoformat() if self._last_frame else None,
            "last_error": self._last_error,
            "fps": self._fps,
            "mode": "remote",
            "url": self._service_url,
        }

    async def async_start(self) -> None:
        """Start the supervisor task."""
        self._closing = False
        self._task = self.hass.async_create_background_task(
            self._run(), name=f"qr_rtsp remote {self._service_url}"
        )

    async def async_stop(self) -> None:
        """Stop the supervisor."""
        self._closing = True
        self._state = "stopped"
        self._connected = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        """Supervisor loop: (re)connect to the service with backoff."""
        backoff = 1
        while not self._closing:
            self._state = "connecting"
            self._connected = False
            try:
                await self._run_once()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE0001 - keep the supervisor alive
                self._last_error = str(err)
                _LOGGER.warning("Remote scanner error: %s", err)

            if self._closing:
                break
            self._state = "reconnecting"
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF)

    async def _run_once(self) -> None:
        """One WebSocket session: send start, relay events until it ends."""
        session = async_get_clientsession(self.hass)
        headers = {"Authorization": f"Bearer {self._secret}"}
        async with session.ws_connect(
            self._service_url, headers=headers, heartbeat=30
        ) as ws:
            await ws.send_json(
                {
                    "type": "start",
                    "stream_url": self._stream_url,
                    "rtsp_transport": self._transport,
                    "fps": self._fps,
                    "width": self._width,
                    "detectors": ["qr"],
                }
            )
            async for msg in ws:
                if self._closing:
                    break
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        self._handle(json.loads(msg.data))
                    except (ValueError, TypeError):
                        _LOGGER.debug("Ignoring malformed message: %s", msg.data)
                elif msg.type in (
                    aiohttp.WSMsgType.ERROR,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSING,
                ):
                    break

    def _handle(self, data: dict) -> None:
        """Process one decoded message from the service."""
        self._last_frame = dt_util.utcnow()  # any message means the service is alive
        msg_type = data.get("type")
        if msg_type == "status":
            if state := data.get("state"):
                self._state = state
                self._connected = state == "streaming"
            if "frames" in data:
                self._frames = data["frames"]
            self._last_error = data.get("last_error")
        elif msg_type == "scan":
            self._state = "streaming"
            self._connected = True
            payload = data.get("payload")
            if not payload:
                return
            symbol_type = data.get("symbol_type", "QRCODE")
            now = self.hass.loop.time()
            last = self._last_seen.get(payload)
            if last is not None and (now - last) < self._cooldown:
                return
            self._last_seen[payload] = now
            self._on_scan(payload, symbol_type)
        elif msg_type == "error":
            self._last_error = data.get("message")
