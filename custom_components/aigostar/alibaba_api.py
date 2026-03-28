"""
Alibaba Cloud IoT API client (eu-central-1) with x-ca-signature authentication.
Includes full login flow: email + password -> iotToken.

Login flow (reverse-engineered from the AigoSmart Android APK):
  1. POST uc.aigostar.com/v1.0/connect/token       -> access_token
  2. GET  uc.aigostar.com/v1.0/connect/authorize    -> authCode
  3. POST api.link.aliyun.com/living/account/region/get -> oaApiGatewayEndpoint
  4. POST {oaHost}/api/prd/loginbyoauth.json        -> sid (OA session)
  5. POST api-iot/account/createSessionByAuthCode    -> iotToken + refreshToken
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
import urllib.parse
import urllib.request
import urllib.error
import uuid
from typing import Any

_LOGGER = logging.getLogger(__name__)

# --- Alibaba Cloud IoT Gateway ---
BASE_URL     = "https://eu-central-1.api-iot.aliyuncs.com"
PATH_GET     = "/thing/properties/get"
PATH_SET     = "/thing/properties/set"
PATH_DEVICES = "/uc/listBindingByAccount"
PATH_CREATE_SESSION = "/account/createSessionByAuthCode"
PATH_REFRESH = "/account/checkOrRefreshSession"
CONTENT_TYPE = "application/json; charset=UTF-8"
ACCEPT       = "application/json; charset=UTF-8"

# --- Region discovery & OAuth login ---
REGION_API_HOST = "https://api.link.aliyun.com"
REGION_API_PATH = "/living/account/region/get"
OA_LOGIN_PATH   = "/api/prd/loginbyoauth.json"
OA_HOST_FALLBACK = "living-account.eu-central-1.aliyuncs.com"

# --- Aigostar User Center ---
UC_BASE      = "https://uc.aigostar.com"
UC_LOGIN     = "/v1.0/connect/token"
UC_AUTHORIZE = "/v1.0/connect/authorize"

# OAuth client credentials (from APK)
CLIENT_ID     = "C28098DEE9664BABBB9AE8E6E47505B0"
CLIENT_SECRET = "C3575D1E-7A5F-411F-920D-5C469AA53AB7"
SMARTAPP_ID   = "smartapp"

# AES key for password encryption (SHA1 signing cert + "0000" padding)
AES_KEY = "tCx8BA0yKVr+NbBChH928URAV90=0000"

# Headers required by the Android app's OkHttp interceptor
UC_APP_KEY   = "smart-android-v1"
UC_TENANT_ID = "1000"


# =====================================================================
#  UC Signature (MD5-based, from RetrofitServiceManager interceptor)
# =====================================================================

def _uc_md5(text: str) -> str:
    """MD5 hex uppercase, matching SignatureUtil.md5() from the APK."""
    return hashlib.md5(text.encode("utf-8")).hexdigest().upper()


def _uc_sign_request(method: str, url: str, timestamp: str) -> str:
    """
    Compute the Signature header as the Android interceptor does.
    SignKey = AppKey + mAppsecret + timestamp + METHOD + url [+ sortedParams]
    Signature = MD5(SignKey).toUpperCase()
    """
    sorted_params = ""
    base_url = url
    if "?" in url:
        base_url, qs = url.split("?", 1)
        pairs = {}
        for part in qs.split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                pairs[k] = v
        sorted_entries = sorted(pairs.items(), key=lambda x: x[0])
        sorted_params = ",".join(f"{k}{v}" for k, v in sorted_entries)

    sign_key = UC_APP_KEY + AES_KEY + timestamp + method.upper() + base_url
    if sorted_params:
        sign_key += sorted_params
    return _uc_md5(sign_key)


def _uc_headers(method: str, url: str) -> dict[str, str]:
    """Generate AppKey/Timestamp/TenantId/Signature headers for UC API."""
    ts = str(int(time.time() * 1000))
    return {
        "AppKey": UC_APP_KEY,
        "Timestamp": ts,
        "TenantId": UC_TENANT_ID,
        "Signature": _uc_sign_request(method, url, ts),
    }


# =====================================================================
#  AES encryption (password)
# =====================================================================

def encrypt_password(password: str) -> str:
    """Encrypt password using AES-256-CBC with zero IV (matches AigoSmart app)."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives import padding as sym_padding

    key = AES_KEY.encode("utf-8")
    iv = b"\x00" * 16
    padder = sym_padding.PKCS7(128).padder()
    padded = padder.update(password.encode("utf-8")) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    ct = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(ct).decode("ascii")


# =====================================================================
#  Aigostar Smart API (verification code endpoints)
# =====================================================================

SMART_API_BASE = "https://smartapi.aigostar.com"
PATH_SEND_CODE = "/message/v1.1/security/sendcode/anonymous"
PATH_VERIFY_CODE = "/message/v1.1/security/verify/anonymous"

# Deterministic device ID (persists across restarts to avoid repeated security codes)
_DEVICE_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "aigostar.homeassistant")).replace("-", "")


def _smart_api_post(path: str, body: dict) -> dict:
    """POST JSON to smartapi.aigostar.com with signature headers."""
    url = SMART_API_BASE + path
    body_bytes = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=UTF-8"}
    headers.update(_uc_headers("POST", url))

    req = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            return json.loads(raw) if raw.strip() else {"ok": True}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            err = json.loads(raw)
        except Exception:
            raise ValueError(f"SmartAPI HTTP {exc.code}: {raw[:300]}") from exc
        code = err.get("code", "")
        if code == "SENDCODE_INTERVAL_IS_TOO_SHORT":
            _LOGGER.debug("Verification code already sent, waiting 60s")
            return {"ok": True, "already_sent": True}
        raise ValueError(f"SmartAPI error: {err}") from exc


def send_verification_code_sync(email: str) -> dict:
    """Send a verification code to the given email for LoginSecurity."""
    account_type = "email" if "@" in email else "phone_number"
    username = email.strip() if "@" in email else email
    body = {
        "send_to": username,
        "account_type": account_type,
        "action": "LoginSecurity",
        "re_send_count": 0,
        "captcha_token": "",
    }
    result = _smart_api_post(PATH_SEND_CODE, body)
    _LOGGER.info("Verification code sent to %s: %s", email, result)
    return result


def check_security_verify_sync(email: str, code: str) -> dict:
    """Verify the code entered by the user."""
    account_type = "email" if "@" in email else "phone_number"
    username = email.strip() if "@" in email else email
    body = {
        "send_to": username,
        "account_type": account_type,
        "action": "LoginSecurity",
        "input_code": code,
    }
    result = _smart_api_post(PATH_VERIFY_CODE, body)
    _LOGGER.info("Code verification result: %s", result)
    return result


# =====================================================================
#  Step 1: Login -> access_token
# =====================================================================

class NeedSecurityCodeError(Exception):
    """Raised when the server requires an email verification code."""
    pass


def _uc_login_sync(email: str, password: str, security_code: str = "") -> dict:
    """POST v1.0/connect/token -> {access_token, refresh_token, user_id}."""
    encrypted_pw = encrypt_password(password)

    account_type = "email" if "@" in email else "phone_number"
    username = email.strip() if "@" in email else email

    form_params = {
        "account_type": account_type,
        "username": username,
        "password": encrypted_pw,
        "grant_type": "password",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "cuid": _DEVICE_ID,
    }
    if security_code:
        form_params["security_code"] = security_code

    form_data = urllib.parse.urlencode(form_params).encode("utf-8")

    login_url = UC_BASE + UC_LOGIN
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    headers.update(_uc_headers("POST", login_url))

    req = urllib.request.Request(
        login_url,
        data=form_data,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            err = json.loads(raw)
        except Exception:
            raise ValueError(f"Login failed: HTTP {exc.code}: {raw[:300]}") from exc
        uc_code = err.get("code", "")
        if uc_code == "UC/NEED_SECURITY_CODE":
            raise NeedSecurityCodeError(err.get("message", "Security code required"))
        msg = err.get("error_description", err.get("message", err.get("error", str(raw[:300]))))
        raise ValueError(f"Login failed: {msg}") from exc

    if "access_token" not in result:
        raise ValueError(f"Login failed: no access_token in response: {result}")
    return result


# =====================================================================
#  Step 2: Authorize -> authCode
# =====================================================================

def _uc_authorize_sync(access_token: str) -> str:
    """GET v1.0/connect/authorize -> authorization code."""
    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": SMARTAPP_ID,
        "redirect_uri": "none",
        "scope": "openid profile",
        "response_mode": "json",
    })
    authorize_url = UC_BASE + UC_AUTHORIZE + "?" + params
    headers = {"Authorization": f"Bearer {access_token}"}
    headers.update(_uc_headers("GET", authorize_url))

    req = urllib.request.Request(
        authorize_url,
        headers=headers,
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        raise ValueError(f"Authorize failed: HTTP {exc.code}: {raw[:300]}") from exc

    code = result.get("code", "")
    if not code:
        raise ValueError(f"Authorize failed: no code in response: {result}")
    return code


# =====================================================================
#  x-ca-signature helpers (shared by IoT API calls)
# =====================================================================

def _content_md5(body_bytes: bytes) -> str:
    return base64.b64encode(hashlib.md5(body_bytes).digest()).decode()


def _build_canonical(
    method: str, path: str, content_md5: str, date: str,
    sign_headers: dict[str, str],
) -> str:
    sorted_keys = sorted(sign_headers.keys())
    canonical_hdrs = "\n".join(f"{k}:{sign_headers[k]}" for k in sorted_keys)
    return (
        f"{method}\n{ACCEPT}\n{content_md5}\n{CONTENT_TYPE}\n{date}\n"
        f"{canonical_hdrs}\n{path}"
    )


def _sign(app_secret: str, canonical: str) -> str:
    mac = hmac.new(app_secret.encode(), canonical.encode(), hashlib.sha1)
    return base64.b64encode(mac.digest()).decode()


def _call_sync(
    path: str, params: dict, app_key: str, app_secret: str,
    iot_token: str | None, api_ver: str = "1.0.0",
    base_url: str | None = None,
) -> dict:
    body_dict: dict[str, Any] = {
        "id": str(uuid.uuid4()).upper(),
        "version": "1.0.0",
        "params": params,
        "request": {
            "language": "en-US",
            "appKey": app_key,
            "apiVer": api_ver,
        },
    }
    if iot_token:
        body_dict["request"]["iotToken"] = iot_token

    body_bytes = json.dumps(body_dict, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    content_md5 = _content_md5(body_bytes)
    timestamp_ms = str(int(time.time() * 1000))
    nonce = str(uuid.uuid4()).upper()
    date = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime())

    sign_headers = {
        "x-ca-key": app_key, "x-ca-nonce": nonce, "x-ca-stage": "RELEASE",
        "x-ca-timestamp": timestamp_ms, "x-ca-version": "1",
    }
    canonical = _build_canonical("POST", path, content_md5, date, sign_headers)
    signature = _sign(app_secret, canonical)

    sorted_keys = sorted(sign_headers.keys())
    headers = {
        "Content-Type": CONTENT_TYPE, "Accept": ACCEPT,
        "Content-MD5": content_md5, "Date": date,
        "X-Ca-Key": app_key, "X-Ca-Nonce": nonce,
        "X-Ca-Timestamp": timestamp_ms, "X-Ca-Stage": "RELEASE",
        "X-Ca-Version": "1",
        "X-Ca-Signature-Headers": ",".join(sorted_keys),
        "X-Ca-Signature-Method": "HmacSHA1",
        "X-Ca-Signature": signature,
    }

    url = (base_url or BASE_URL) + path
    req = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            result = json.loads(raw)
        except Exception:
            raise ValueError(f"HTTP {exc.code}: {raw[:300]}") from exc
    except Exception as exc:
        raise ValueError(f"Network error: {exc}") from exc

    code = result.get("code", -1)
    if code != 200:
        raise ValueError(f"Alibaba IoT error code={code}: {result}")
    return result


# =====================================================================
#  Step 3: Region discovery -> OA host
# =====================================================================

def _resolve_oa_host_sync(auth_code: str, app_key: str, app_secret: str) -> str:
    """Query the region API to determine the correct OA host for OAuth login."""
    try:
        result = _call_sync(
            REGION_API_PATH,
            {"type": "THIRD_AUTHCODE", "authCode": auth_code},
            app_key, app_secret, iot_token=None, api_ver="1.0.2",
            base_url=REGION_API_HOST,
        )
        oa_host = result.get("data", {}).get("oaApiGatewayEndpoint", "")
        if oa_host:
            _LOGGER.info("Region discovery: OA host = %s", oa_host)
            return oa_host
    except Exception as exc:
        _LOGGER.warning("Region discovery failed: %s — using fallback", exc)
    return OA_HOST_FALLBACK


# =====================================================================
#  Step 4: OAuth login -> OA session (sid)
# =====================================================================

def _oa_login_sync(
    auth_code: str, oa_host: str, app_key: str, app_secret: str,
) -> str:
    """POST loginbyoauth on the OA gateway -> returns sid (OA session ID)."""
    content_type_form = "application/x-www-form-urlencoded; charset=UTF-8"
    accept_json = "application/json; charset=UTF-8"

    oauth_map = {
        "oauthPlateform": 23,
        "accessToken": None,
        "openId": None,
        "oauthAppKey": app_key,
        "tokenType": None,
        "authCode": auth_code,
        "userData": None,
    }
    form_params = {
        "loginByOauthRequest": json.dumps(oauth_map, separators=(",", ":"), ensure_ascii=False),
    }
    body_bytes = urllib.parse.urlencode(form_params).encode("utf-8")

    timestamp_ms = str(int(time.time() * 1000))
    nonce = str(uuid.uuid4()).upper()
    date = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime())

    sign_headers = {
        "x-ca-key": app_key, "x-ca-nonce": nonce, "x-ca-stage": "RELEASE",
        "x-ca-timestamp": timestamp_ms, "x-ca-version": "1",
    }
    sorted_fp = sorted(form_params.items())
    resource = OA_LOGIN_PATH + "?" + "&".join(f"{k}={v}" for k, v in sorted_fp)

    sorted_keys = sorted(sign_headers.keys())
    canonical_hdrs = "\n".join(f"{k}:{sign_headers[k]}" for k in sorted_keys)
    canonical = (
        f"POST\n{accept_json}\n\n{content_type_form}\n{date}\n"
        f"{canonical_hdrs}\n{resource}"
    )
    signature = _sign(app_secret, canonical)

    headers = {
        "Content-Type": content_type_form, "Accept": accept_json,
        "Date": date,
        "X-Ca-Key": app_key, "X-Ca-Nonce": nonce,
        "X-Ca-Timestamp": timestamp_ms, "X-Ca-Stage": "RELEASE",
        "X-Ca-Version": "1",
        "X-Ca-Signature-Headers": ",".join(sorted_keys),
        "X-Ca-Signature-Method": "HmacSHA1",
        "X-Ca-Signature": signature,
    }

    url = f"https://{oa_host}{OA_LOGIN_PATH}"
    req = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        raise ValueError(f"OA login failed: HTTP {exc.code}: {raw[:300]}") from exc

    # Extract sid from nested response
    data = result.get("data", {})
    login_data = data.get("data", {}).get("loginSuccessResult", {})
    sid = login_data.get("sid", "")
    if not sid:
        code = data.get("code", "?")
        msg = data.get("message", "")
        raise ValueError(f"OA login failed: code={code}, message={msg}")
    _LOGGER.debug("OA login OK: sid=%s...", sid[:8])
    return sid


# =====================================================================
#  Step 5: OA session -> iotToken (via IoT API Gateway)
# =====================================================================

def _create_session_sync(sid: str, app_key: str, app_secret: str) -> dict:
    """Exchange OA session ID for iotToken via IoT API Gateway."""
    result = _call_sync(
        PATH_CREATE_SESSION,
        {"request": {"authCode": sid, "appKey": app_key, "accountType": "OA_SESSION"}},
        app_key, app_secret, iot_token=None, api_ver="1.0.4",
    )
    data = result.get("data", {})
    if not data or "iotToken" not in data:
        raise ValueError(f"createSession failed: no iotToken in response: {result}")
    return data


# =====================================================================
#  Full login (5 steps)
# =====================================================================

def full_login_sync(
    email: str, password: str, app_key: str, app_secret: str,
    security_code: str = "",
) -> dict:
    """
    Full login: email + password -> iotToken.
    Returns dict with: iotToken, refreshToken, identityId, iotTokenExpire.
    Raises NeedSecurityCodeError if email verification is required.
    """
    # Step 1: UC login
    login_info = _uc_login_sync(email, password, security_code)
    access_token = login_info["access_token"]
    _LOGGER.info("Aigostar login OK (user_id=%s)", login_info.get("user_id", "?"))

    # Step 2: UC authorize -> authCode
    auth_code = _uc_authorize_sync(access_token)
    _LOGGER.debug("Aigostar authorize OK (code=%s...)", auth_code[:8])

    # Step 3: Region discovery -> OA host
    oa_host = _resolve_oa_host_sync(auth_code, app_key, app_secret)

    # Step 4: OAuth login -> sid (OA session)
    sid = _oa_login_sync(auth_code, oa_host, app_key, app_secret)

    # Step 5: Create IoT session using sid
    session = _create_session_sync(sid, app_key, app_secret)
    _LOGGER.info("Aigostar iotToken obtained (expires in %ss)", session.get("iotTokenExpire", "?"))
    return session


# =====================================================================
#  Refresh iotToken
# =====================================================================

def refresh_iot_token_sync(
    refresh_token: str, identity_id: str, app_key: str, app_secret: str,
) -> dict:
    """Renew the iotToken using the refreshToken."""
    result = _call_sync(
        PATH_REFRESH,
        {"refreshToken": refresh_token, "identityId": identity_id},
        app_key, app_secret, iot_token=None, api_ver="1.0.4",
    )
    data = result.get("data", {})
    if "iotToken" not in data:
        raise ValueError(f"Token refresh failed: no iotToken in response: {result}")
    _LOGGER.info("Aigostar iotToken refreshed (expires in %ss)", data.get("iotTokenExpire", "?"))
    return data


# =====================================================================
#  Device list & client
# =====================================================================

def list_devices_sync(app_key: str, app_secret: str, iot_token: str) -> list[dict]:
    """List all devices bound to the account."""
    result = _call_sync(
        PATH_DEVICES,
        {"pageNo": 1, "pageSize": 100},
        app_key, app_secret, iot_token, api_ver="1.0.8",
    )
    return result.get("data", {}).get("data", [])


class AlibabaIoTClient:
    """Synchronous client for a single Alibaba Cloud IoT device."""

    def __init__(self, iot_id: str, iot_token: str, app_key: str, app_secret: str) -> None:
        self.iot_id = iot_id
        self.iot_token = iot_token
        self.app_key = app_key
        self.app_secret = app_secret

    def get_properties_sync(self) -> dict:
        result = _call_sync(
            PATH_GET, {"iotId": self.iot_id},
            self.app_key, self.app_secret, self.iot_token,
        )
        data = result.get("data", {})
        return {k: v["value"] for k, v in data.items() if isinstance(v, dict) and "value" in v}

    def set_properties_sync(self, items: dict) -> None:
        _call_sync(
            PATH_SET, {"iotId": self.iot_id, "items": items},
            self.app_key, self.app_secret, self.iot_token,
        )
        _LOGGER.debug("SET OK [%s]: %s", self.iot_id, items)
