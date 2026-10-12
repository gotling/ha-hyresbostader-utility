"""Coordinator polling all meters from the Hyresbostäder IMD site."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    HyresbostaderAPI,
    HyresbostaderAuthError,
    HyresbostaderConnectionError,
    MeterData,
    has_readings_until,
)
from .const import CONF_USER_ID, DOMAIN, UPDATE_INTERVAL
from .statistics import async_import_statistics

_LOGGER = logging.getLogger(__name__)

type HyresbostaderConfigEntry = ConfigEntry[HyresbostaderCoordinator]


class HyresbostaderCoordinator(DataUpdateCoordinator[dict[str, MeterData]]):
    """Fetches readings for all meters, keyed by MeterData.key."""

    config_entry: HyresbostaderConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: HyresbostaderConfigEntry,
        api: HyresbostaderAPI,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.api = api

    async def _async_update_data(self) -> dict[str, MeterData]:
        # Meters report once a day, so once yesterday's readings are in there
        # is nothing new until tomorrow.
        if self.data and has_readings_until(self.data.values(), dt_util.start_of_local_day()):
            _LOGGER.debug("Readings for yesterday already fetched, skipping until tomorrow")
            return self.data
        meters = await self._async_fetch()
        data = {meter.key: meter for meter in meters.values()}
        await async_import_statistics(self.hass, data)
        return data

    async def _async_fetch(self) -> dict[str, MeterData]:
        user_id = self.config_entry.data[CONF_USER_ID]
        try:
            return await self.api.async_get_meter_data(user_id)
        except HyresbostaderConnectionError as err:
            first_error = err

        # The stored userId may no longer be valid; log in to rediscover it.
        try:
            await self.api.async_login()
            new_user_id = await self.api.async_get_user_id()
            if new_user_id == user_id:
                raise UpdateFailed(str(first_error)) from first_error
            _LOGGER.debug("IMD userId changed, updating config entry")
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, CONF_USER_ID: new_user_id},
            )
            return await self.api.async_get_meter_data(new_user_id)
        except HyresbostaderAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except HyresbostaderConnectionError as err:
            raise UpdateFailed(str(err)) from err
