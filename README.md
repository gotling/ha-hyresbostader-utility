# Hyresbostäder Utility

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Validate](https://github.com/gotling/ha-hyresbostader-utility/actions/workflows/validate.yml/badge.svg)](https://github.com/gotling/ha-hyresbostader-utility/actions/workflows/validate.yml)

Home Assistant integration that reads the individual metering ("Min förbrukning") of your apartment from the [Hyresbostäder](https://www.hyresbostader.se) tenant portal: cold and hot water usage and indoor temperature.

This is an unofficial integration and is not affiliated with Hyresbostäder.

> [!NOTE]
> This integration has been developed with the help of [Claude](https://claude.com/claude-code), an AI assistant by Anthropic.

## Installation

### HACS

1. In HACS, open the menu (⋮) → **Custom repositories**.
2. Add `https://github.com/gotling/ha-hyresbostader-utility` with type **Integration**.
3. Search for **Hyresbostäder Utility**, download it and restart Home Assistant.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=gotling&repository=ha-hyresbostader-utility&category=integration)

### Manual

Copy `custom_components/hyresbostader_utility` into the `custom_components` folder of your Home Assistant configuration and restart Home Assistant.

## Configuration

**Settings → Devices & services → Add integration → Hyresbostäder Utility**, then log in with the personal identity number and password you use for "Mina sidor" on mittsokande.hyresbostader.se. BankID login is not supported; set a password on "Mina sidor" first if you only use BankID.

## Sensors

One device is created per meter found on the portal. Meters report once a day: the portal is polled hourly until yesterday's readings are in, then not again until the next day. A device for a meter that is no longer on the portal can be deleted from its device page.

| Meter | Sensors |
| --- | --- |
| Kallvatten, Varmvatten (liters) | Current week, Previous week, Current year, Previous year, Last reading |
| Temperatur (°C) | Temperature, Current week average, Previous week average, Last reading |
| Other meters (e.g. Tvättar, Sköljmedel) | Current week, Previous week, Current year, Previous year, Last reading |

Period sensors have the period label, start, end and the per-day (week) or per-month (year) values as attributes.

## Daily statistics

The daily consumption of each meter (except temperature) is imported into long-term statistics, dated to the day it belongs to rather than when it was fetched. The statistics are named e.g. "Hyresbostäder Kallvatten" (`hyresbostader_utility:<meter id>_consumption`) and can be used as a water source in the energy dashboard or in a statistics graph card with a daily period. Each update rewrites the current and previous week, so days that are reported late are filled in; history before the first import is not available.

## Development

```sh
uv run pytest                                   # unit tests
HB_USERNAME=... HB_PASSWORD=... uv run scripts/try_login.py   # check against the live portal
docker compose up -d                            # local Home Assistant on http://localhost:8123
docker compose restart homeassistant            # reload after code changes
```
