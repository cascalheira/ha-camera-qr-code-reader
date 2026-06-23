# QR Vision Service

A small, fast **remote video-processing worker** for the Home Assistant
[QR Code RTSP Reader](../) integration.

The Raspberry Pi struggles to software-decode H.264 *and* scan for QR codes. This
service moves that work to a stronger machine (e.g. a Proxmox VM, optionally with
GPU passthrough). Home Assistant stays the control plane — all the UI, rules,
history and status live there — and just **offloads the pixels**.

```
Home Assistant  ──ws (Bearer secret)──▶  qr-vision-service  ──ffmpeg──▶  RTSP camera
   (rules/UI)   ◀──scan/status events──        (decode + QR)
```

## Protocol (JSON over WebSocket)

- Endpoint: `GET /ws` — requires header `Authorization: Bearer <secret>`.
- Health: `GET /health` → `ok` (no auth).

Client → service (sent once on connect):

```json
{ "type": "start", "stream_url": "rtsp://…", "rtsp_transport": "tcp",
  "fps": 4, "width": 640, "detectors": ["qr"] }
```

Service → client:

```json
{ "type": "status", "state": "streaming", "frames": 1234, "last_error": null }
{ "type": "scan", "payload": "ha-camera-qr-code-reader|…", "symbol_type": "QRCODE", "ts": "2026-…" }
{ "type": "error", "message": "…" }
```

The service debounces repeats (same payload within 3 s) and reconnects to the
camera on its own with backoff.

## Configuration (environment)

| Variable | Default | Notes |
| --- | --- | --- |
| `QR_SERVICE_SECRET` | – | **Required**, ≥16 chars. Must match the integration's "Secret key". |
| `BIND_ADDR` | `0.0.0.0:8723` | Listen address. |
| `FFMPEG_PATH` | `ffmpeg` | Path to ffmpeg. |
| `FFMPEG_HWACCEL` | – | Optional `-hwaccel`: `vaapi` (AMD/Intel), `cuda` (NVIDIA), `qsv` (Intel). |
| `FFMPEG_HWACCEL_DEVICE` | – | Render node for VAAPI, e.g. `/dev/dri/renderD128`. |
| `RUST_LOG` | `info` | Log filter. |

See [`.env.example`](.env.example).

## Run

### Docker (recommended)

```bash
docker build -t qr-vision-service .
docker run -d --name qr-vision -p 8723:8723 \
  -e QR_SERVICE_SECRET="$(openssl rand -hex 24)" \
  qr-vision-service
```

### Compiled binary with a .env file

`cp .env.example .env`, edit it, then run the binary **from the directory that
contains `.env`** — it is loaded automatically (real env vars still win):

```bash
cargo build --release
cp .env.example .env && $EDITOR .env
./target/release/qr-vision-service
```

(`dotenvy` searches the working directory and its parents, so running from the
project root works too.)

### From source

```bash
QR_SERVICE_SECRET=your-long-secret cargo run --release
```

### As a systemd service

```ini
# /etc/systemd/system/qr-vision.service
[Service]
EnvironmentFile=/opt/qr-vision/.env
ExecStart=/opt/qr-vision/qr-vision-service
Restart=always

[Install]
WantedBy=multi-user.target
```

Then in Home Assistant, add/configure the integration with **Processing mode =
Remote**, **Service URL = `ws://<vm-host>:8723/ws`**, and the same **Secret key**.

## GPU passthrough (Proxmox)

1. Pass the GPU into the VM (PCIe passthrough).
2. Pick the right `-hwaccel` for your hardware:

   **AMD or Intel (Linux → VAAPI):**
   ```bash
   docker run -d --name qr-vision -p 8723:8723 \
     --device /dev/dri \
     -e QR_SERVICE_SECRET=… \
     -e FFMPEG_HWACCEL=vaapi \
     -e FFMPEG_HWACCEL_DEVICE=/dev/dri/renderD128 \
     qr-vision-service
   ```
   The Debian-based image already ships an ffmpeg with VAAPI; you only need the
   `/dev/dri` device passed in (and the AMD/Intel kernel driver + Mesa on the
   host). Check the node with `ls /dev/dri` (usually `renderD128`).

   **NVIDIA (CUDA):** base the image on a cuda-enabled ffmpeg, run with
   `--gpus all`, and set `FFMPEG_HWACCEL=cuda`.

The protocol and integration are unchanged — only decode moves to the GPU. GPU
matters most for the planned **people detection** (ML inference), which will be
added as another entry in `detectors`.

## Security

- Always set a strong `QR_SERVICE_SECRET`; the WebSocket rejects anything else.
- The stream URL (with camera credentials) travels over this connection — keep
  the service on a trusted network, or terminate TLS in front of it and use a
  `wss://` Service URL in Home Assistant.

## Status

Implemented: QR detection. Planned: `people` detector (GPU-accelerated).
