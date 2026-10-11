"""Sensors for water usage and temperature from the Hyresbostäder IMD site."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import MeterData, Period
from .const import DOMAIN, MANUFACTURER, UNIT_CELSIUS, UNIT_LITER
from .coordinator import HyresbostaderConfigEntry, HyresbostaderCoordinator

# The coordinator does all fetching.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class HyresbostaderSensorEntityDescription(SensorEntityDescription):
    """Sensor reading one value from a meter's data."""

    value_fn: Callable[[MeterData], float | datetime | None]
    attrs_fn: Callable[[MeterData], dict[str, Any]] | None = None


def _period_attrs(period_fn: Callable[[MeterData], Period]) -> Callable[[MeterData], dict[str, Any]]:
    """Expose the period label, bounds and per day/month values as attributes."""

    def attrs(data: MeterData) -> dict[str, Any]:
        period = period_fn(data)
        return {
            "period": period.label,
            "start": period.start,
            "end": period.end,
            "values": {item.label: item.value for item in period.values},
        }

    return attrs


LAST_READING = HyresbostaderSensorEntityDescription(
    key="last_reading",
    translation_key="last_reading",
    device_class=SensorDeviceClass.TIMESTAMP,
    entity_category=EntityCategory.DIAGNOSTIC,
    value_fn=lambda data: data.last_count_time,
)


def _total_sensors(
    unit: str | None, device_class: SensorDeviceClass | None
) -> tuple[HyresbostaderSensorEntityDescription, ...]:
    """Week and year totals for consumption meters (water, laundry)."""
    return (
        HyresbostaderSensorEntityDescription(
            key="current_week",
            translation_key="current_week",
            native_unit_of_measurement=unit,
            device_class=device_class,
            # Resets every Monday, which total_increasing treats as a new cycle.
            state_class=SensorStateClass.TOTAL_INCREASING,
            value_fn=lambda data: data.current_week.total,
            attrs_fn=_period_attrs(lambda data: data.current_week),
        ),
        HyresbostaderSensorEntityDescription(
            key="previous_week",
            translation_key="previous_week",
            native_unit_of_measurement=unit,
            device_class=device_class,
            value_fn=lambda data: data.previous_week.total,
            attrs_fn=_period_attrs(lambda data: data.previous_week),
        ),
        HyresbostaderSensorEntityDescription(
            key="current_year",
            translation_key="current_year",
            native_unit_of_measurement=unit,
            device_class=device_class,
            # Usable in the energy dashboard; resets every new year.
            state_class=SensorStateClass.TOTAL_INCREASING,
            value_fn=lambda data: data.current_year.total,
            attrs_fn=_period_attrs(lambda data: data.current_year),
        ),
        HyresbostaderSensorEntityDescription(
            key="previous_year",
            translation_key="previous_year",
            native_unit_of_measurement=unit,
            device_class=device_class,
            value_fn=lambda data: data.previous_year.total,
            attrs_fn=_period_attrs(lambda data: data.previous_year),
        ),
        LAST_READING,
    )


WATER_SENSORS = _total_sensors(UnitOfVolume.LITERS, SensorDeviceClass.WATER)
COUNT_SENSORS = _total_sensors(None, None)

# The year summary for temperature averages in empty months, so only weeks are used.
TEMPERATURE_SENSORS: tuple[HyresbostaderSensorEntityDescription, ...] = (
    HyresbostaderSensorEntityDescription(
        key="temperature",
        name=None,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.last_count,
    ),
    HyresbostaderSensorEntityDescription(
        key="current_week_average",
        translation_key="current_week_average",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        value_fn=lambda data: data.current_week.total,
        attrs_fn=_period_attrs(lambda data: data.current_week),
    ),
    HyresbostaderSensorEntityDescription(
        key="previous_week_average",
        translation_key="previous_week_average",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        value_fn=lambda data: data.previous_week.total,
        attrs_fn=_period_attrs(lambda data: data.previous_week),
    ),
    LAST_READING,
)


def _sensors_for(data: MeterData) -> tuple[HyresbostaderSensorEntityDescription, ...]:
    if data.unit == UNIT_LITER:
        return WATER_SENSORS
    if data.unit == UNIT_CELSIUS:
        return TEMPERATURE_SENSORS
    return COUNT_SENSORS


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HyresbostaderConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add sensors for every meter on the IMD dashboard."""
    coordinator = entry.runtime_data
    async_add_entities(
        HyresbostaderSensor(coordinator, data, description)
        for data in coordinator.data.values()
        for description in _sensors_for(data)
    )


class HyresbostaderSensor(CoordinatorEntity[HyresbostaderCoordinator], SensorEntity):
    """One value of one meter."""

    entity_description: HyresbostaderSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: HyresbostaderCoordinator,
        data: MeterData,
        description: HyresbostaderSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._meter_id = data.id
        self._attr_unique_id = f"{data.id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, data.id)},
            name=data.name,
            manufacturer=MANUFACTURER,
            model=data.name,
            serial_number=data.number,
        )

    @property
    def _data(self) -> MeterData | None:
        return self.coordinator.data.get(self._meter_id)

    @property
    def available(self) -> bool:
        return super().available and self._data is not None

    @property
    def native_value(self) -> float | datetime | None:
        if (data := self._data) is None:
            return None
        return self.entity_description.value_fn(data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if (data := self._data) is None or self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(data)
