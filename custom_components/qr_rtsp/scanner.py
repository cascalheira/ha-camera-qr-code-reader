"""RTSP frame grabber + QR decoder.

A single long-running ffmpeg process pulls low-rate, downscaled MJPEG frames
from the RTSP stream. Each complete JPEG is handed to libzbar (via pyzbar) in
the executor. Detected payloads are debounced per-value and reported through a
callback. The ffmpeg process is supervised and reconnects with backoff.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable
from io import BytesIO

from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# JPEG start-of-image / end-of-image markers used to frame the MJPEG pipe.
_JPEG_SOI = b"\xff\xd8"
_JPEG_EOI = b"\xff\xd9"

_READ_CHUNK = 65536
_MAX_BACKOFF = 30


def _decode_frame(jpeg: bytes) -> list[tuple[str, str]]:
    """Decode QR codes from a single JPEG frame. Runs in the executor."""
    # Imported lazily so import errors surface at runtime, not at HA startup.
    from PIL import Image  # noqa: PLC0415
    from pyzbar.pyzbar import ZBarSymbol, decode  # noqa: PLC0415

    try:
        image = Image.open(BytesIO(jpeg))
        image.load()
    except Exception:  # noqa: BLE0001 - corrupt/partial frame, just skip it
        return []

    results = []
    for sym in decode(image, symbols=[ZBarSymbol.QRCODE]):
        try:
            payload = sym.data.decode("utf-8")
        except UnicodeDecodeError:
            payload = sym.data.decode("latin-1", "replace")
        results.append((payload, sym.type))
    return results


class QrStreamScanner:
    """Supervises an ffmpeg pull of an RTSP stream and decodes QR codes."""

    def __init__(
        self,
        hass: HomeAssistant,
        binary: str,
        url: str,
        *,
        fps: float,
        width: int,
        cooldown: float,
        transport: str,
        on_scan: Callable[[str, str], None],
    ) -> None:
        """Initialize the scanner."""
        self.hass = hass
        self._binary = binary
        self._url = url
        self._fps = fps
        self._width = width
        self._cooldown = cooldown
        self._transport = transport
        self._on_scan = on_scan

        self._task: asyncio.Task | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._closing = False
        self._last_seen: dict[str, float] = {}
        self._stderr_tail: deque[str] = deque(maxlen=10)

    def _build_args(self) -> list[str]:
        """Build the ffmpeg command line."""
        vf = f"fps={self._fps}"
        if self._width > 0:
            vf += f",scale={self._width}:-2"
        return [
            self._binary,
            "-nostdin",
            "-loglevel",
            "error",
            "-rtsp_transport",
            self._transport,
            "-i",
            self._url,
            "-an",
            "-vf",
            vf,
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "-q:v",
            "5",
            "pipe:1",
        ]

    async def async_start(self) -> None:
        """Start the supervisor task."""
        self._closing = False
        self._task = self.hass.async_create_background_task(
            self._run(), name=f"qr_rtsp scanner {self._url}"
        )

    async def async_stop(self) -> None:
        """Stop the supervisor and tear down ffmpeg."""
        self._closing = True
        await self._terminate_proc()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _terminate_proc(self) -> None:
        """Terminate the running ffmpeg process if any."""
        proc = self._proc
        self._proc = None
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5)
        except (TimeoutError, asyncio.TimeoutError):
            proc.kill()
        except ProcessLookupError:
            pass

    async def _run(self) -> None:
        """Supervisor loop: (re)connect to the stream with exponential backoff."""
        backoff = 1
        while not self._closing:
            try:
                await self._run_once()
                backoff = 1
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE0001 - keep the supervisor alive
                _LOGGER.warning("QR RTSP stream error: %s", err)
            finally:
                await self._terminate_proc()

            if self._closing:
                break
            tail = "; ".join(self._stderr_tail)
            _LOGGER.debug(
                "Reconnecting to %s in %ss%s",
                self._url,
                backoff,
                f" (ffmpeg: {tail})" if tail else "",
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF)

    async def _run_once(self) -> None:
        """Run one ffmpeg session, reading and decoding frames until it ends."""
        self._proc = await asyncio.create_subprocess_exec(
            *self._build_args(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert self._proc.stdout is not None
        stderr_task = self.hass.async_create_task(self._drain_stderr())

        buffer = bytearray()
        try:
            while not self._closing:
                chunk = await self._proc.stdout.read(_READ_CHUNK)
                if not chunk:
                    break  # ffmpeg exited; supervisor will reconnect
                buffer.extend(chunk)
                for frame in _extract_frames(buffer):
                    await self._process_frame(frame)
        finally:
            stderr_task.cancel()

    async def _drain_stderr(self) -> None:
        """Capture ffmpeg stderr for diagnostics."""
        if self._proc is None or self._proc.stderr is None:
            return
        try:
            async for raw in self._proc.stderr:
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    self._stderr_tail.append(line)
                    _LOGGER.debug("ffmpeg: %s", line)
        except asyncio.CancelledError:
            pass

    async def _process_frame(self, frame: bytes) -> None:
        """Decode a frame and dispatch any new payloads."""
        codes = await self.hass.async_add_executor_job(_decode_frame, frame)
        if not codes:
            return
        now = self.hass.loop.time()
        for payload, symbol_type in codes:
            last = self._last_seen.get(payload)
            if last is not None and (now - last) < self._cooldown:
                continue
            self._last_seen[payload] = now
            _LOGGER.debug("QR detected: %s (%s)", payload, symbol_type)
            self._on_scan(payload, symbol_type)


def _extract_frames(buffer: bytearray) -> list[bytes]:
    """Pull complete JPEG frames out of the buffer, leaving any partial tail."""
    frames: list[bytes] = []
    while True:
        start = buffer.find(_JPEG_SOI)
        if start == -1:
            buffer.clear()
            break
        end = buffer.find(_JPEG_EOI, start + 2)
        if end == -1:
            # Drop leading garbage but keep the incomplete frame for next read.
            if start:
                del buffer[:start]
            break
        frames.append(bytes(buffer[start : end + 2]))
        del buffer[: end + 2]
    return frames
