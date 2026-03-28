"""Aigostar integration for Home Assistant — automatic login."""
from __future__ import annotations

import logging
import time
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.event import async_track_time_interval

from .alibaba_api import full_login_sync, list_devices_sync, refresh_iot_token_sync
from .const import APP_KEY, APP_SECRET, CONF_EMAIL, CONF_PASSWORD, DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["light"]
TOKEN_REFRESH_INTERVAL = timedelta(hours=1)
DEVICE_SYNC_INTERVAL = timedelta(minutes=5)

SERVICE_SYNC = "sync_devices"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = dict(entry.data)
    email = data[CONF_EMAIL]
    password = data[CONF_PASSWORD]

    # Full login
    session = await hass.async_add_executor_job(
        full_login_sync, email, password, APP_KEY, APP_SECRET,
    )

    iot_token = session["iotToken"]
    refresh_token = session.get("refreshToken", "")
    identity_id = session.get("identityId", "")
    token_expire = int(session.get("iotTokenExpire", 7200))
    token_created = time.time()

    # Discover devices
    devices = await hass.async_add_executor_job(
        list_devices_sync, APP_KEY, APP_SECRET, iot_token,
    )
    _LOGGER.info("Aigostar: login OK, %d devices discovered", len(devices))

    # Shared state
    entry_data = {
        "devices": devices,
        "iot_token": iot_token,
        "refresh_token": refresh_token,
        "identity_id": identity_id,
        "token_expire": token_expire,
        "token_created": token_created,
        "email": email,
        "password": password,
        "app_key": APP_KEY,
        "app_secret": APP_SECRET,
    }

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = entry_data

    # Periodic token refresh
    async def _refresh_token(_now=None):
        ed = hass.data[DOMAIN].get(entry.entry_id)
        if not ed:
            return

        elapsed = time.time() - ed["token_created"]
        if elapsed < ed["token_expire"] - 1800:
            return

        try:
            if ed["refresh_token"] and ed["identity_id"]:
                new_session = await hass.async_add_executor_job(
                    refresh_iot_token_sync,
                    ed["refresh_token"], ed["identity_id"],
                    APP_KEY, APP_SECRET,
                )
            else:
                new_session = await hass.async_add_executor_job(
                    full_login_sync,
                    ed["email"], ed["password"],
                    APP_KEY, APP_SECRET,
                )

            new_token = new_session["iotToken"]
            ed["iot_token"] = new_token
            ed["refresh_token"] = new_session.get("refreshToken", ed["refresh_token"])
            ed["identity_id"] = new_session.get("identityId", ed["identity_id"])
            ed["token_expire"] = int(new_session.get("iotTokenExpire", 7200))
            ed["token_created"] = time.time()

            for entity in hass.data.get(f"{DOMAIN}_entities", {}).get(entry.entry_id, []):
                entity.update_token(new_token)

            _LOGGER.info("Aigostar: iotToken refreshed successfully")

        except Exception as exc:
            _LOGGER.warning("Aigostar: token refresh failed, retrying with full login: %s", exc)
            try:
                new_session = await hass.async_add_executor_job(
                    full_login_sync,
                    ed["email"], ed["password"],
                    APP_KEY, APP_SECRET,
                )
                new_token = new_session["iotToken"]
                ed["iot_token"] = new_token
                ed["refresh_token"] = new_session.get("refreshToken", "")
                ed["identity_id"] = new_session.get("identityId", "")
                ed["token_expire"] = int(new_session.get("iotTokenExpire", 7200))
                ed["token_created"] = time.time()

                for entity in hass.data.get(f"{DOMAIN}_entities", {}).get(entry.entry_id, []):
                    entity.update_token(new_token)

                _LOGGER.info("Aigostar: iotToken obtained via re-login")
            except Exception as exc2:
                _LOGGER.error("Aigostar: re-login also failed: %s", exc2)

    # Periodic device sync: check for new devices
    async def _periodic_sync(_now=None):
        ed = hass.data[DOMAIN].get(entry.entry_id)
        if not ed:
            return
        try:
            devices = await hass.async_add_executor_job(
                list_devices_sync, APP_KEY, APP_SECRET, ed["iot_token"],
            )
            known_ids = {d["iotId"] for d in ed["devices"] if "iotId" in d}
            new_ids = {d["iotId"] for d in devices if d.get("iotId")} - known_ids
            if new_ids:
                _LOGGER.info("Aigostar auto-sync: %d new devices found, reloading integration", len(new_ids))
                await hass.config_entries.async_reload(entry.entry_id)
        except Exception as exc:
            _LOGGER.debug("Aigostar auto-sync failed: %s", exc)

    unsub_refresh = async_track_time_interval(hass, _refresh_token, TOKEN_REFRESH_INTERVAL)
    unsub_sync = async_track_time_interval(hass, _periodic_sync, DEVICE_SYNC_INTERVAL)
    entry_data["unsub_refresh"] = unsub_refresh
    entry_data["unsub_sync"] = unsub_sync

    # Manual service: aigostar.sync_devices (reloads the integration)
    async def _handle_sync_service(call: ServiceCall) -> None:
        for eid in list(hass.data.get(DOMAIN, {})):
            cfg_entry = hass.config_entries.async_get_entry(eid)
            if cfg_entry:
                _LOGGER.info("Aigostar sync_devices: reloading integration %s", cfg_entry.title)
                await hass.config_entries.async_reload(eid)

    if not hass.services.has_service(DOMAIN, SERVICE_SYNC):
        hass.services.async_register(DOMAIN, SERVICE_SYNC, _handle_sync_service)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    entry_data = hass.data[DOMAIN].get(entry.entry_id, {})
    for key in ("unsub_refresh", "unsub_sync"):
        unsub = entry_data.get(key)
        if unsub:
            unsub()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        hass.data.get(f"{DOMAIN}_entities", {}).pop(entry.entry_id, None)
        # Remove service if no entries remain
        if not hass.data.get(DOMAIN):
            hass.services.async_remove(DOMAIN, SERVICE_SYNC)
    return unload_ok
