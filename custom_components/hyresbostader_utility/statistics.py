"""Import daily consumption into long-term statistics, dated to the day it belongs to."""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util, slugify
from homeassistant.util.unit_conversion import VolumeConverter

from .api import MeterData
from .const import DOMAIN, MANUFACTURER, UNIT_CELSIUS, UNIT_LITER

_LOGGER = logging.getLogger(__name__)


def statistic_id(meter_key: str) -> str:
    """External statistic holding the daily consumption of a meter."""
    return f"{DOMAIN}:{slugify(meter_key)}_consumption"


async def async_import_statistics(hass: HomeAssistant, meters: dict[str, MeterData]) -> None:
    """Import the daily values of the previous and current week for consumption meters."""
    for data in meters.values():
        if data.unit != UNIT_CELSIUS:
            await _async_import_meter(hass, data)


async def _async_import_meter(hass: HomeAssistant, data: MeterData) -> None:
    days = data.previous_week.daily_values() + data.current_week.daily_values()
    if not days:
        return
    stat_id = statistic_id(data.key)
    starts = [dt_util.start_of_local_day(day) for day, _ in days]

    # Every import rewrites the whole two week window, since a day can be
    # reported late or revised. Continue from the sum just before the window.
    base = await _async_sum_before(hass, stat_id, starts[0])
    statistics: list[StatisticData] = []
    total = base
    for start, (_, value) in zip(starts, days, strict=True):
        total += value
        statistics.append(StatisticData(start=start, state=value, sum=total))

    is_liter = data.unit == UNIT_LITER
    metadata = StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=f"{MANUFACTURER} {data.name}",
        source=DOMAIN,
        statistic_id=stat_id,
        unit_class=VolumeConverter.UNIT_CLASS if is_liter else None,
        unit_of_measurement=UnitOfVolume.LITERS if is_liter else None,
    )
    _LOGGER.debug("Importing %d days into %s", len(statistics), stat_id)
    async_add_external_statistics(hass, metadata, statistics)


async def _async_sum_before(hass: HomeAssistant, stat_id: str, window_start: datetime) -> float:
    """Return the cumulative sum of all rows before window_start."""
    recorder = get_instance(hass)
    last = await recorder.async_add_executor_job(
        get_last_statistics, hass, 1, stat_id, True, {"sum"}
    )
    if not (rows := last.get(stat_id)):
        return 0.0
    if rows[0]["start"] < window_start.timestamp():
        return rows[0]["sum"] or 0.0
    # Rows already exist inside the window: the first one's sum minus its own
    # value is what came before it.
    existing = await recorder.async_add_executor_job(
        statistics_during_period,
        hass,
        window_start,
        None,
        {stat_id},
        "hour",
        None,
        {"sum", "state"},
    )
    first = existing[stat_id][0]
    return (first["sum"] or 0.0) - (first["state"] or 0.0)
