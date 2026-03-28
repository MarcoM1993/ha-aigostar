# Aigostar Smart Lights for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](LICENSE)
[![GitHub Release](https://img.shields.io/github/v/release/MarcoM1993/ha-aigostar?style=for-the-badge)](https://github.com/MarcoM1993/ha-aigostar/releases)
A Home Assistant custom integration for **Aigostar smart bulbs** (TG7100C chipset, Alibaba Cloud IoT backend).

Control your Aigostar lights directly from Home Assistant — no local flashing required. The integration communicates with Alibaba Cloud IoT using the same protocol as the AigoSmart app.

## Features

- **Cloud-based control** via Alibaba Cloud IoT API (EU region)
- **Automatic device discovery** — all bulbs linked to your AigoSmart account are added automatically
- **Periodic device sync** — new bulbs added via the AigoSmart app are detected every 5 minutes
- **Manual sync service** — `aigostar_local.sync_devices` to force device re-discovery
- **Automatic token refresh** — iotToken is renewed before expiration
- **Brightness control** (1–100%)
- **Color temperature** (2700K warm – 6500K cool)
- **Email verification** support (when the server requires a security code)
- **Multilingual UI** — English and Italian translations included

## Supported Devices

| Device | Chipset | Protocol | Status |
|--------|---------|----------|--------|
| Aigostar smart bulb (E27/E14/GU10) | TG7100C (Bouffalo Lab) | Alibaba Cloud IoT | Tested |

> Other Aigostar smart devices using the same Alibaba Cloud IoT backend may work but have not been tested.

## Installation

### HACS (Recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=MarcoM1993&repository=ha-aigostar&category=integration)

Click the button above, or manually:

1. Open HACS in Home Assistant
2. Go to **Integrations** → **⋮** (top right) → **Custom repositories**
3. Add this repository URL: `https://github.com/MarcoM1993/ha-aigostar`
4. Select **Integration** as the category
5. Click **Add**, then find **Aigostar Smart Lights** in the list and install it
6. Restart Home Assistant

### Manual

1. Download or clone this repository
2. Copy the `custom_components/aigostar_local` folder to your Home Assistant `config/custom_components/` directory
3. Restart Home Assistant

## Configuration

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=aigostar_local)

Or manually:

1. Go to **Settings** → **Devices & Services** → **Add Integration**
2. Search for **Aigostar Smart Lights**
3. Enter your **AigoSmart** account email and password
4. If the server requests a verification code, enter the code sent to your email
5. All bulbs linked to your account will be discovered automatically

## Services

### `aigostar_local.sync_devices`

Force re-discovery of all devices from the Aigostar cloud. New devices are added automatically. You can call this service from:

- **Developer Tools** → **Services**
- Automations or scripts

## How It Works

The integration uses the same 5-step login flow as the AigoSmart Android app (reverse-engineered via APK decompilation):

1. **UC Login** — authenticate with email/password on Aigostar User Center
2. **UC Authorize** — obtain an authorization code
3. **Region Discovery** — resolve the correct EU OAuth API gateway
4. **OAuth Login** — exchange the authCode for an OA session ID
5. **IoT Session** — exchange the session ID for an iotToken

Device control is performed via the Alibaba Cloud IoT API Gateway (`eu-central-1.api-iot.aliyuncs.com`) using x-ca-signature authentication.

## Troubleshooting

### Login fails
- Verify your email and password are correct (same as the AigoSmart app)
- If you get a verification code prompt, check your email inbox (including spam)

### Devices show as unavailable
- The bulb must be powered on and connected to Wi-Fi
- Check that the bulb works in the AigoSmart app first

### New bulbs not appearing
- Wait up to 5 minutes for auto-sync, or call `aigostar_local.sync_devices`
- You can also reload the integration: **Settings** → **Integrations** → **Aigostar** → **⋮** → **Reload**

## Support This Project

This integration was built from scratch by reverse engineering the AigoSmart app — no public API, no documentation, just hours of packet sniffing and APK decompilation.

If it saved you time or you just enjoy using it, consider buying me a coffee. It keeps the project alive and motivates new features and device support.

<a href="https://paypal.me/marcomicheli93">
  <img src="https://img.shields.io/badge/Support%20via-PayPal-00457C?style=for-the-badge&logo=paypal&logoColor=white" alt="Support via PayPal" height="40">
</a>

Every contribution — no matter how small — is truly appreciated. Thank you!

## Disclaimer

This integration is unofficial and not affiliated with Aigostar or Alibaba Cloud. It was developed through reverse engineering of the AigoSmart Android app for personal and educational use. Use at your own risk.

## License

[MIT](LICENSE)
