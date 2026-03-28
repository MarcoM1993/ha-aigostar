# Aigostar Smart Lights — Claude Code Project Guide

## Project Overview

Custom Home Assistant integration for Aigostar smart bulbs (TG7100C chipset).
These bulbs use **Alibaba Cloud IoT** as their backend (not Tuya cloud, despite the Tuya-OEM chip).
The integration communicates via the Alibaba Cloud IoT API Gateway using x-ca-signature authentication.

## Architecture

### Login Flow (5 steps, reverse-engineered from AigoSmart APK)

1. **UC Login** — `POST uc.aigostar.com/v1.0/connect/token` → access_token
2. **UC Authorize** — `GET uc.aigostar.com/v1.0/connect/authorize` → authCode
3. **Region Discovery** — `POST api.link.aliyun.com/living/account/region/get` → OA host
4. **OAuth Login** — `POST {oaHost}/api/prd/loginbyoauth.json` → sid (OA session)
5. **IoT Session** — `POST eu-central-1.api-iot.aliyuncs.com/account/createSessionByAuthCode` → iotToken

### API Authentication

- **UC API** (uc.aigostar.com): Custom MD5 signature → `MD5(AppKey + AESKey + timestamp + METHOD + URL + sortedParams)`
- **IoT API** (api-iot.aliyuncs.com): x-ca-signature → `Base64(HMAC-SHA1(canonical_string, AppSecret))`
- **Canonical string format**: `METHOD\nACCEPT\nCONTENT-MD5\nCONTENT-TYPE\nDATE\nCANONICAL-HEADERS\nPATH`
- **Password encryption**: AES-256-CBC, key = `tCx8BA0yKVr+NbBChH928URAV90=0000`, IV = 16 zero bytes

### API Credentials (from APK decompilation via jadx)

- AppKey: `28770785` (Android), `28803202` (iOS)
- Source: `com.aigostar.lib.aigo.api.constants.CommonValueApiConstant` in classes2.dex
- These are public app credentials, not user secrets

### TSL Properties (device data model)

| Property | Type | Range | Description |
|----------|------|-------|-------------|
| LightSwitch | bool | 0/1 | On/off |
| Brightness | int | 1-100 | Percentage |
| ColorTemperature | int | 0-100 | 0=warm 2700K, 100=cool 6500K |
| LightMode | enum | 0/1 | 0=white, 1=color (only white on TG7100C) |

## File Structure

```
custom_components/aigostar/
├── __init__.py      — Entry setup, token refresh, device sync, service registration
├── alibaba_api.py   — Full API client: login flow, device list, property get/set
├── brand/           — Integration icons for HA 2026.3+ (icon.png, icon@2x.png)
├── config_flow.py   — HA config flow: email/password → optional verification code
├── const.py         — Constants: API keys, TSL property names, conversion ranges
├── light.py         — LightEntity: polling, brightness/color_temp control
├── manifest.json    — HA integration metadata
├── services.yaml    — sync_devices service definition
├── strings.json     — UI strings (English, canonical)
└── translations/    — en.json, it.json
```

## Key Technical Details

- **Token refresh**: iotToken expires (default 7200s). Refreshed automatically every hour via `refreshToken` or full re-login as fallback.
- **Device sync**: New devices auto-detected every 5 minutes. Manual sync via `aigostar.sync_devices` service.
- **Polling interval**: 30 seconds (`SCAN_INTERVAL_SECONDS` in const.py).
- **EU region**: All endpoints use eu-central-1. Region is resolved dynamically via the region API.
- **OA login quirk**: `oauthPlateform` must be integer `23`, not string. Field name is intentionally misspelled (matches the API).

## Development Workflow

### Branches
- `main` — stable releases only (tagged `vX.Y.Z`)
- `beta` — pre-releases for testing (tagged `vX.Y.Z-beta.N`)
- `dev` — active development

### Release Process (automated via CI)

Releases are fully automated by `.github/workflows/release.yml`. **Do NOT manually create tags, bump versions, or create GitHub releases.**

How it works:
1. Push/merge commits to `main` (use conventional commit messages: `feat:`, `fix:`, etc.)
2. The CI workflow automatically:
   - Analyzes commit messages since the last tag to determine the version bump (patch/minor/major)
   - Bumps the version in `manifest.json`
   - Creates a `chore(release): vX.Y.Z` commit + tag
   - Creates a GitHub Release with auto-generated changelog
   - Merges main back into `dev`
3. Commits starting with `chore(release):` are skipped by the CI to avoid infinite loops

Version bump rules (conventional commits):
- `fix:` → patch bump (e.g. 1.2.1 → 1.2.2)
- `feat:` → minor bump (e.g. 1.2.1 → 1.3.0)
- `feat!:` or `BREAKING CHANGE` → major bump (e.g. 1.2.1 → 2.0.0)

**Important**: When merging to main, ensure the last commit message is NOT `chore(release):` — otherwise the CI will skip the release.

### Deploy to Home Assistant (dev/test)
```bash
# Set up SSH password file
python3 -c "f=open('/tmp/.sshpw','w'); f.write('YOUR_PASSWORD'); f.close()"

# Sync files to HA
sshpass -f /tmp/.sshpw rsync -av -e "ssh -o PreferredAuthentications=password -o StrictHostKeyChecking=no" \
  custom_components/aigostar/ USER@HA_IP:/config/custom_components/aigostar/

# Restart HA via API
curl -s -X POST -H "Authorization: Bearer YOUR_TOKEN" \
  http://HA_IP:8123/api/services/homeassistant/restart
```

### Testing commands via HA API
```bash
# Turn on with brightness and color temp
curl -X POST -H "Authorization: Bearer TOKEN" -H "Content-Type: application/json" \
  -d '{"entity_id": "light.ENTITY", "brightness": 128, "color_temp_kelvin": 3500}' \
  http://HA_IP:8123/api/services/light/turn_on

# Call sync service
curl -X POST -H "Authorization: Bearer TOKEN" -H "Content-Type: application/json" \
  -d '{}' http://HA_IP:8123/api/services/aigostar/sync_devices
```

## Language

- Code, comments, docstrings, log messages: **English**
- User communicates in **Italian** — respond in Italian
- All code, comments, docstrings, log messages, and CLI output must be in **English**
- UI translations exist for both English and Italian
