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

## Generating secure QR codes

The `qr_rtsp.generate_code` service mints a random code, renders a downloadable
PNG, and (optionally) registers it as an access rule in one step.

Payload format:

```
ha-camera-qr-code-reader|<your name>|<random token>
```

The random token comes from Python's `secrets` (cryptographically secure) and
is URL-safe, so it never collides with the `|` separator. **Complexity** is the
number of random bytes — `16` = 128 bits (recommended), up to `64`. Higher
entropy is stronger but produces a denser code that's harder to scan from a
distance.

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
| `image_url` | Downloadable URL, e.g. `/local/qr_rtsp/guest-weekend-1a2b3c4d.png`. |
| `image_path` | Absolute file path on disk (under `config/www/qr_rtsp/`). |
| `registered_entries` | Entry IDs the rule was added to (empty if not registering). |

The PNG is written to `config/www/qr_rtsp/` and served at `/local/qr_rtsp/…`,
so you can open/download it in a browser, attach it to a notification, or show
it in a dashboard `picture` card. When `register: true`, the code is enforced by
the same validity engine described below — no copy/paste needed.

> `register: true` requires at least one `device_id`. Without registering, the
> service just produces the payload + image.

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

Event data: `payload`, `type`, `name`, `entry_id`, `authorized`, `reason`, `rule`.

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

## License

MIT
