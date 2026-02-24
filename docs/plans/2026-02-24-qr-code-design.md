# QR Code on E-ink Display

## Goal
Add a WiFi-join QR code to the Drewtopia dashboard display, as large as possible, without removing existing elements (header, names).

## Design

### Layout
Two-column dashboard:
- **Header**: "Drewtopia" centered across full 400px width (unchanged)
- **Left column** (~155px): "Home" label + user name list
- **Right column**: WiFi QR code (~200x200px), vertically centered in content area
- **Text**: "drew.com" in small font near QR code

Welcome screen: unchanged (no QR).
Idle screen: same two-column layout with "No one is home" + QR.

### QR Code
- WiFi join string: `WIFI:T:WPA;S:<ssid>;P:<password>;;`
- Credentials from `WIFI_SSID` and `WIFI_PASSWORD` in backend/.env
- Generated with `qrcode` Python library, rendered as black-on-white PIL image
- Pasted onto e-ink display image in palette mode

### Files Changed
1. `backend/drivers/eink.py` — two-column `render_dashboard()` with QR
2. `backend/.env` — add WIFI_SSID, WIFI_PASSWORD
3. `backend/requirements.txt` — add `qrcode[pil]`
