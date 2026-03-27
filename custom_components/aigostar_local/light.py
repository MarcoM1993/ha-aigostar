"""
Light platform for Aigostar (Alibaba Cloud IoT) — multi-device support.

Supported TSL properties (TG7100C):
  - LightSwitch      bool  0/1
  - Brightness       int   1-100
  - ColorTemperature int   0-100  (0=warm 2700K, 100=cool 6500K)
  - LightMode        enum  0=white 1=color (only white supported on this model)
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .alibaba_api import AlibabaIoTClient
from .const import (
    AIGO_BRIGHT_MAX,
    AIGO_BRIGHT_MIN,
    DOMAIN,
    HA_BRIGHT_MAX,
    KELVIN_COOL,
    KELVIN_WARM,
    PROP_BRIGHTNESS,
    PROP_COLOR_TEMP,
    PROP_LIGHT_MODE,
    PROP_SWITCH,
    SCAN_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=SCAN_INTERVAL_SECONDS)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entry_data = hass.data[DOMAIN][entry.entry_id]
    devices = entry_data["devices"]
    iot_token = entry_data["iot_token"]
    app_key = entry_data["app_key"]
    app_secret = entry_data["app_secret"]

    entities = []
    for dev in devices:
        iot_id = dev.get("iotId", "")
        nick = dev.get("nickName") or dev.get("deviceName") or iot_id[:12]
        status = dev.get("status", 0)

        client = AlibabaIoTClient(
            iot_id=iot_id,
            iot_token=iot_token,
            app_key=app_key,
            app_secret=app_secret,
        )
        entities.append(AigostarLight(client, iot_id, nick, online=(status == 1), raw_device=dev))

    # Register entities for token refresh
    hass.data.setdefault(f"{DOMAIN}_entities", {})
    hass.data[f"{DOMAIN}_entities"][entry.entry_id] = entities

    _LOGGER.info("Aigostar: creating %d light entities", len(entities))
    async_add_entities(entities, update_before_add=True)


class AigostarLight(LightEntity):
    """Aigostar smart bulb via Alibaba Cloud IoT."""

    _attr_has_entity_name = True
    _attr_name = None  # Use device name as entity name
    _attr_supported_color_modes = {ColorMode.COLOR_TEMP}
    _attr_color_mode            = ColorMode.COLOR_TEMP
    _attr_min_color_temp_kelvin = KELVIN_WARM
    _attr_max_color_temp_kelvin = KELVIN_COOL

    def __init__(
        self, client: AlibabaIoTClient, iot_id: str, name: str,
        online: bool, raw_device: dict | None = None,
    ) -> None:
        self._client = client
        self._attr_unique_id = iot_id

        raw = raw_device or {}
        product_name = raw.get("productName") or raw.get("categoryName") or "Smart Bulb"
        fw_version = raw.get("firmwareVersion") or raw.get("moduleVersion") or None

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, iot_id)},
            name=name,
            manufacturer="Aigostar",
            model=product_name,
            sw_version=fw_version,
        )

        self._is_on:        bool = False
        self._brightness:   int  = 255
        self._color_temp_k: int  = 4000
        self._available:    bool = online

    def update_token(self, new_token: str) -> None:
        """Update the iotToken after a refresh."""
        self._client.iot_token = new_token

    # ------------------------------------------------------------------
    # Conversions
    # ------------------------------------------------------------------

    @staticmethod
    def _aigo_to_ha_brightness(v: int) -> int:
        pct = (v - AIGO_BRIGHT_MIN) / (AIGO_BRIGHT_MAX - AIGO_BRIGHT_MIN)
        return max(1, round(pct * HA_BRIGHT_MAX))

    @staticmethod
    def _ha_to_aigo_brightness(v: int) -> int:
        pct = v / HA_BRIGHT_MAX
        return max(AIGO_BRIGHT_MIN, min(AIGO_BRIGHT_MAX, round(pct * AIGO_BRIGHT_MAX)))

    @staticmethod
    def _aigo_to_kelvin(v: int) -> int:
        """0 = warm 2700K, 100 = cool 6500K."""
        pct = v / 100.0
        return round(KELVIN_WARM + pct * (KELVIN_COOL - KELVIN_WARM))

    @staticmethod
    def _kelvin_to_aigo(k: int) -> int:
        pct = (k - KELVIN_WARM) / (KELVIN_COOL - KELVIN_WARM)
        return max(0, min(100, round(pct * 100)))

    # ------------------------------------------------------------------
    # HA properties
    # ------------------------------------------------------------------

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def brightness(self) -> int:
        return self._brightness

    @property
    def color_temp_kelvin(self) -> int:
        return self._color_temp_k

    @property
    def available(self) -> bool:
        return self._available

    # ------------------------------------------------------------------
    # Update (polling)
    # ------------------------------------------------------------------

    def _apply_props(self, props: dict) -> None:
        if PROP_SWITCH in props:
            self._is_on = bool(props[PROP_SWITCH])
        if PROP_BRIGHTNESS in props:
            self._brightness = self._aigo_to_ha_brightness(int(props[PROP_BRIGHTNESS]))
        if PROP_COLOR_TEMP in props:
            self._color_temp_k = self._aigo_to_kelvin(int(props[PROP_COLOR_TEMP]))

    def update(self) -> None:
        try:
            props = self._client.get_properties_sync()
            self._apply_props(props)
            self._available = True
        except Exception as exc:
            _LOGGER.warning("Aigostar [%s] update failed: %s", self._attr_unique_id, exc)
            self._available = False

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def turn_on(self, **kwargs: Any) -> None:
        try:
            items: dict[str, Any] = {PROP_SWITCH: 1}

            if ATTR_BRIGHTNESS in kwargs:
                ha_b = int(kwargs[ATTR_BRIGHTNESS])
                items[PROP_BRIGHTNESS] = self._ha_to_aigo_brightness(ha_b)
                self._brightness = ha_b

            if ATTR_COLOR_TEMP_KELVIN in kwargs:
                k = int(kwargs[ATTR_COLOR_TEMP_KELVIN])
                items[PROP_COLOR_TEMP] = self._kelvin_to_aigo(k)
                items[PROP_LIGHT_MODE] = 0  # white mode
                self._color_temp_k = k

            self._client.set_properties_sync(items)
            self._is_on     = True
            self._available = True

        except Exception as exc:
            _LOGGER.error("Aigostar turn_on failed [%s]: %s", self._attr_unique_id, exc)
            self._available = False

    def turn_off(self, **kwargs: Any) -> None:
        try:
            self._client.set_properties_sync({PROP_SWITCH: 0})
            self._is_on     = False
            self._available = True
        except Exception as exc:
            _LOGGER.error("Aigostar turn_off failed [%s]: %s", self._attr_unique_id, exc)
            self._available = False
