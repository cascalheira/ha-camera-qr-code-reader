# QR Code RTSP Reader

A Home Assistant custom integration that continuously watches an **RTSP camera
stream**, decodes any **QR code** it sees, and lets you react to it — toggle a
switch, run a script, anything you can do in an automation.

Distributed via [HACS](https://hacs.xyz/).

## How it works

```
RTSP stream ── ffmpeg (low fps, downscaled MJPEG) ── pyzbar/libzbar ── event + sensor
```

A single `ffmpeg` process pulls a few downscaled frames per second from the
stream; each frame is decoded with `libzbar`. When a code is found the
integration:

1. fires a `qr_rtsp_scanned` **event**, and
2. updates a **`sensor.<name>_last_qr_code`** entity (payload in the state,
   full details in attributes).

You map codes to actions in your own automations — maximum flexibility, no
extra config UI to fight with.

## Processing mode: local or remote

You can choose where the heavy video work happens:

- **Local** (default) — Home Assistant decodes the RTSP stream and scans for QR
  codes itself. Simplest, but a Raspberry Pi 5 has no hardware H.264 decoder, so
  it can struggle.
- **Remote** — offload decoding + detection to the companion
  [**qr-vision-service**](rust-service/) (a small Rust worker), e.g. on a
  Proxmox VM with GPU passthrough. Home Assistant stays the control plane (rules,
  panel, history, status); it just connects over an authenticated WebSocket and
  receives scan events. Same UI, same behavior.

Set this in the integration's setup / **Configure → General settings**:
**Processing mode**, **Service URL** (`ws://<host>:8723/ws`), and a **Secret
key** that must match the service's `QR_SERVICE_SECRET`. See
[`rust-service/README.md`](rust-service/README.md) to run it.

The status bar in the admin panel shows which mode is active (`local` / `remote`)
and whether it's currently streaming.

## Installation (HACS)

1. HACS → ⋮ → **Custom repositories** → add this repo as an **Integration**.
2. Install **QR Code RTSP Reader** and restart Home Assistant.
3. **Settings → Devices & Services → Add Integration → QR Code RTSP Reader**.

## Configuration

| Option | Default | Notes |
| --- | --- | --- |
| RTSP stream URL | – | **Use your camera's low-res substream** for far less CPU. |
| RTSP transport | `tcp` | `tcp` is more reliable; `udp` lower latency. |
| Frames per second | `4` | How often to scan. 2–5 is plenty for held-up codes. |
| Downscale width | `640` | Width in px; height auto. `0` keeps original size. |
| Re-trigger cooldown | `3` s | Min seconds before the *same* payload fires again. |

All except the URL/name are editable later via **Configure**.

## Admin panel

After setup, an admin-only **QR Codes** entry appears in the Home Assistant
sidebar. It's a full management UI (backed by a WebSocket API) where you can:

- **See every code** for each configured reader, with its validity and the
  script bound to it.
- **Add a code** — register a payload with a **title** (free-form description of
  what the code is for), optional validity (dates, weekdays, time window) and a
  **script to run on an authorized scan**.
- **Generate a code** — mint a secure random code, pick its complexity, set its
  validity/script, and **download the QR PNG** right from the dialog.
- **Edit / delete** codes.

It also shows:

- **Live status** — a bar at the top reports whether the stream is
  `Streaming` / `Connecting` / `Reconnecting` / `Stopped`, how long ago the last
  frame arrived, total frames decoded, and the last ffmpeg error (with the
  password redacted from the URL). It refreshes every few seconds, so you can
  confirm the scanner is actually working.
- **History** — a persisted audit log of recent scans (newest first): when,
  which code, authorized/denied, and the reason. Survives restarts (kept in
  `.storage`, last 200 per reader) and can be cleared from the UI.

Rule edits apply live — they do **not** restart the camera stream. (Changing
stream settings like FPS/URL still reloads, as it must.)

### Run a script automatically on scan

Each code can name a `script.*` entity. When that code is scanned **and**
passes its validity check, the integration calls the script for you — no
automation required. (You can still use the `qr_rtsp_scanned` event for anything
more elaborate, e.g. handling denied scans.)

## Generating secure QR codes

The `qr_rtsp.generate_code` service mints a random code, renders a downloadable
PNG, and (optionally) registers it as an access rule in one step.

Payload format — short and opaque on purpose (the name/title is metadata stored
with the rule, **not** embedded in the code):

```
hcqrcr|<random token>
```

The generated PNG is **captioned** with the code's title (or name) underneath,
so a printed code is identifiable at a glance. Captions render with a bundled
font that covers accented characters (see `custom_components/qr_rtsp/fonts/`).

The random token comes from Python's `secrets` (cryptographically secure) and
is URL-safe. **Complexity** is the number of random bytes — `16` = 128 bits
(recommended). Higher entropy is stronger but produces a denser code that is
harder for a cheap camera to read; the codes use medium error correction to stay
sparse. For best scannability keep complexity at 16.

Run it from **Developer Tools → Actions** (tick *"Return response data"* to see
the result), or in a script:

```yaml
action: qr_rtsp.generate_code
data:
  name: guest-weekend
  entropy_bytes: 16
  register: true          # also create an access rule on the device(s)
  device_id: <your qr reader device>
  valid_from: "2026-07-04"
  valid_until: "2026-07-06"
  weekdays: [sat, sun]
  start_time: "08:00:00"
  end_time: "20:00:00"
response_variable: qr
```

Response data:

| Field | Description |
| --- | --- |
| `payload` | The full encoded string. |
| `name` | The name you passed. |
| `random` | The random token only. |
| `image_b64` | The QR PNG, base64-encoded. |
| `registered_entries` | Entry IDs the rule was added to (empty if not registering). |

> `register: true` requires at least one `device_id`. Without registering, the
> service just produces the payload + image.

### The QR images are protected

QR codes are sensitive (the random token *is* the key), so the integration
**never writes them to a public folder**. The image is returned as base64 over
the authenticated service/WebSocket response only. To turn `image_b64` into a
file (e.g. to attach to a notification), decode it yourself, e.g.:

```yaml
- variables:
    qr: "{{ qr }}"            # response_variable from the action above
- service: notify.persistent_notification
  data:
    message: "QR for {{ qr.name }}"
# or write it to a file with a shell_command / python_script using
# base64.b64decode(qr.image_b64)
```

In the **admin panel**, downloading a code streams the PNG over the
authenticated WebSocket and saves it via an in-browser data URL — again, no
public URL is ever created.

## Access rules (validity & schedule)

You can bind specific QR payloads to a **validity window** so a code only works
when you want it to. Go to the integration's **Configure** dialog → **Add a QR
access rule**, and set any combination of:

| Field | Meaning |
| --- | --- |
| QR payload | Exact text encoded in the QR code (the match key). |
| Friendly name | Optional label shown in events/automations. |
| Valid from / until | Inclusive date range. "Until" covers the whole end day. |
| Allowed weekdays | Empty = every day. |
| Allowed from / until (time) | Daily time window. Supports overnight (e.g. 22:00→06:00). |

When a code is scanned, the integration computes a verdict and reports it on
both the event and the sensor:

- `authorized` — `true` / `false`
- `reason` — `ok`, `expired`, `not_yet_valid`, `out_of_schedule`,
  `unknown_code`, or `no_rules`
- `rule` — the matched rule's name (or `null`)

**Codes without a rule:** by default they're authorized (`reason: no_rules`/`ok`),
so the simple "any code triggers" behavior still works. Flip **General settings
→ "Authorize codes that have no rule"** off to make unlisted codes `unknown_code`
(deny-by-default access control).

> The integration only computes the verdict — *you* decide what to do with it.
> Always branch on `authorized` in your automation before taking the action.

## Reacting to a scan

### Event-based (recommended)

```yaml
automation:
  - alias: "Unlock door on authorized QR"
    trigger:
      - platform: event
        event_type: qr_rtsp_scanned
        event_data:
          payload: "open-front-door"   # exact match, optional
    condition:
      - "{{ trigger.event.data.authorized }}"
    action:
      - service: switch.toggle
        target:
          entity_id: switch.front_door
```

Event data: `payload`, `type`, `name`, `entry_id`, `authorized`, `reason`,
`rule`, `title`.

### Sensor-based

Trigger off `sensor.<name>_last_qr_code` state changes and branch on the value
with `choose:` if you prefer a single automation.

## Performance

On a **Raspberry Pi 5**, with a low-res substream at 4 fps, this uses a small
fraction of one core. End-to-end latency (code shown → action) is typically
**0.5–2 s**, dominated by RTSP/network buffering, not decoding. Tips:

- Prefer the camera's **substream** (e.g. 640×480) over the main stream.
- Keep **fps low** (2–5) and **downscale** (`width` 480–640).
- Note: the Pi 5 has **no hardware H.264 decoder**, so decode is on the CPU —
  another reason to use a small substream.

## Dependencies

- The `ffmpeg` integration (bundled with Home Assistant).
- `pyzbar` (auto-installed) → needs the `libzbar0` system library, present in
  Home Assistant OS / Container images.

## Icon / branding

The integration's icon lives in [`brands/`](brands/). HACS and Home Assistant
load icons from the [home-assistant/brands](https://github.com/home-assistant/brands)
repository (keyed by the `qr_rtsp` domain), so submit those assets there to have
the icon appear — see [`brands/README.md`](brands/README.md) for the steps.

## License

MIT
