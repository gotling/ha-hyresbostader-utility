# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# hyresbostader-utility

Home Assistant custom integration that fetches water usage (hot and cold) and temperature from the Hyresbostäder tenant portal (`mittsokande.hyresbostader.se`, "Min förbrukning").

## Commands
- Add dependencies: `uv add <package>` (also add them to `requirements` in `manifest.json` — HA installs from there, not `pyproject.toml`)
- Run local tests/scripts: `uv run <script.py>`
- Run tests: `uv run pytest` (pytest + pytest-asyncio; tests spin up a fake portal with `aiohttp.test_utils.TestServer` — aioresponses is incompatible with aiohttp 3.14)
- Live check (login, userId, meters, meter data): `HB_USERNAME=... HB_PASSWORD=... uv run scripts/try_login.py`
- Local HA: `docker compose up -d` (HA Core, UI on http://localhost:8123; VS Code task "Start Home Assistant"). The integration is bind-mounted into `config/custom_components`, so after a change just `docker compose restart homeassistant`. `config/` is the HA config dir (only `configuration.yaml` is tracked; debug logging for the integration is on). The Supervisor apps devcontainer is not needed for integrations and fails on Docker Desktop for macOS.
- Python >= 3.14 (see `.python-version`). No linter configured yet. Project is non-packaged (`tool.uv.package = false`).

## Layout
- `custom_components/hyresbostader_utility/` — the actual HA integration (this is what gets installed into HA). `api.py` (portal/IMD client, no HA imports), `coordinator.py` (hourly `DataUpdateCoordinator`, re-logs in to rediscover userId on errors), `config_flow.py` (user + reauth), `sensor.py` (one device per meter; sensor set chosen by unit), `translations/` (en, sv; `strings.json` is a copy of en).
- `tests/` — unit tests; `tests/fixtures/` holds real HTML of the login page, a failed-login response, the IMD dashboard, and ChangeMeter, week and year JSON for meters 1001 and 1005.

## API endpoints

### Login
https://mittsokande.hyresbostader.se/User/MyPagesLogin.aspx?returnurl=/minforbrukning

Login with username and password.

### userId and meters (discovered, not hard-coded)

- `GET /Res/Themes/HnabPortal3/Pages/HSO/Energi/IMD_Submit.aspx` on the portal (logged in) → 302 to `https://imd.hyresbostader.se/IMD/?userId=...`. Not logged in → 302 to `/Global/Error.aspx`.
- `GET https://imd.hyresbostader.se/IMD/?userId=...` → dashboard HTML; meters are `ul.meters li.selector[data-meter-id]` with the name as text. Current meters: 1001 Kallvatten, 1002 Varmvatten, 1003 Tvättar, 1004 Sköljmedel, 1005 Temperatur.
- The IMD site is authorized by `userId` alone (no portal cookies needed), so portal login is only needed to discover the userId.

### Get one meter for the current and last week

First meter needs to be changed, then fetch. Verified behaviour:
- `GET /IMD/?userId=...` (dashboard) must be loaded first in the session; without it both JSON endpoints return 500.
- The selected meter is server-side session state, so calls must be sequential (`_imd_lock`). An unknown meterId gives 500 on ChangeMeter but GetCurrentAndPreviousPeriod then silently returns the *previous* meter, so the ChangeMeter `id` is checked.
- ChangeMeter JSON: `id`, `name`, `unit` ("Liter", "°C", "st"), `number`, `lastCountNum`, `lastCountTime`. All water values are in liters. Tvättar/Sköljmedel have no data for this user (all 0/null).
- GetCurrentAndPreviousPeriod JSON: `currentPeriod`/`previousPeriod` with `periodLabel`, `start`, `end`, `summary.value` (week total; average for temperature), `data[]` (7 days, `value` null if not reported).
- `GET /IMD/Dashboard/ChangePeriodType?periodName=År|Vecka&userId=...` switches the period type (session state, kept across ChangeMeter and across refreshes, so always set explicitly). It returns the current period JSON; GetCurrentAndPreviousPeriod then returns `currentPeriod`/`previousPeriod` for that type. "År" = current/previous year with 12 monthly `data[]` values (`labelLong` = "Januari"...). For temperature the year `summary` is an average including empty months (e.g. 5,68), so it's not meaningful.
- Numbers are strings with Swedish decimal comma ("21,46"); dates are `/Date(epochms+0200)/`.

#### Warm water

https://imd.hyresbostader.se/IMD/Dashboard/ChangeMeter?meterId=1002&userId=TestUserId1%3d

https://imd.hyresbostader.se/IMD/Dashboard/GetCurrentAndPreviousPeriod?userId=TestUserId1%3d

#### Cold water

https://imd.hyresbostader.se/IMD/Dashboard/ChangeMeter?meterId=1001&userId=TestUserId1%3d

https://imd.hyresbostader.se/IMD/Dashboard/GetCurrentAndPreviousPeriod?userId=TestUserId1%3d

#### Temperature

https://imd.hyresbostader.se/IMD/Dashboard/ChangeMeter?meterId=1005&userId=TestUserId1%3d

https://imd.hyresbostader.se/IMD/Dashboard/GetCurrentAndPreviousPeriod?userId=TestUserId1%3d


## Home Assistant Architecture
- Domain: `hyresbostader_utility`
- Intended design: a `DataUpdateCoordinator` polls the site once and distributes water/temperature metrics to separate sensor entities. Credentials come from the config flow.
- Uses `aiohttp` for async web scraping/API calls (pass HA's shared session via `async_get_clientsession`). No blocking `requests`.
- `api.py` (`HyresbostaderAPI.async_login`): GETs the login page, posts back all hidden/text inputs of the ASP.NET form (`__VIEWSTATE` etc.) plus `...LoginControl1$txtUserID`/`txtPassword`/`btnLogin` (field names verified against the live site). Failed login returns 200 with the login form again, so "password field still present" = bad credentials. Raises `HyresbostaderAuthError` (bad credentials) or `HyresbostaderConnectionError` (network/HTTP/page-structure); config flow should map these to `invalid_auth`/`cannot_connect`.
- `async_get_user_id()` (after login) reads the userId from the IMD_Submit redirect; `async_get_meters(user_id)` parses the dashboard into `Meter(id, name)` list. `imd_base_url` is injectable like `base_url` for tests.
- `async_get_meter_data(user_id)` opens the dashboard, then for each period type (Vecka, År) calls ChangePeriodType once and ChangeMeter + GetCurrentAndPreviousPeriod per meter. Returns `dict[meter_id, MeterData]` (`last_count`, `last_count_time`, `current_week`/`previous_week`/`current_year`/`previous_year` as `Period(label, start, end, total, values)`).
- Auth is cookie-based, so the integration should use `async_create_clientsession` (own cookie jar), not the shared `async_get_clientsession`.

## Token Rules
- CRITICAL: Never write chatty intro/outro text or summarize code changes.
- CRITICAL: Return ONLY modified or new Python blocks. No unchanged lines.
