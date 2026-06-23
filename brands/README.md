# Brand assets

These are the icon assets for the **QR Code RTSP Reader** (`qr_rtsp`)
integration, used by Home Assistant and HACS to display its logo.

| File | Size |
| --- | --- |
| `custom_integrations/qr_rtsp/icon.png` | 256×256 |
| `custom_integrations/qr_rtsp/icon@2x.png` | 512×512 |

## How the icon appears in HACS / Home Assistant

HACS and Home Assistant do **not** read icons from this repository. They load
them from the official [home-assistant/brands](https://github.com/home-assistant/brands)
repository, keyed by the integration domain (`qr_rtsp`).

To make the icon show up, submit these files to that repo:

1. Fork `home-assistant/brands`.
2. Copy this folder's contents to `custom_integrations/qr_rtsp/` in your fork:
   - `custom_integrations/qr_rtsp/icon.png`
   - `custom_integrations/qr_rtsp/icon@2x.png`
3. Open a pull request. Once merged, the icon appears automatically (no release
   needed) — Home Assistant caches brands, so allow some time.

Notes:
- `icon.png` must be ≤ 256×256 and `icon@2x.png` exactly double it.
- Backgrounds are transparent and the artwork is trimmed to the edges, per the
  [brands guidelines](https://github.com/home-assistant/brands#requirements).
- A `logo.png` is optional; the icon is used when no logo is provided.
