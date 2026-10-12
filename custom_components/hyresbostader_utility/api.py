"""Client for the Hyresbostäder tenant portal."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

import aiohttp
from bs4 import BeautifulSoup

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://mittsokande.hyresbostader.se"
LOGIN_PATH = "/User/MyPagesLogin.aspx?returnurl=/minforbrukning"
# Redirects a logged-in user to the IMD dashboard with their userId in the query string.
IMD_SUBMIT_PATH = "/Res/Themes/HnabPortal3/Pages/HSO/Energi/IMD_Submit.aspx"

IMD_BASE_URL = "https://imd.hyresbostader.se"
IMD_DASHBOARD_PATH = "/IMD/"
# Selects the meter in the server-side IMD session; following period calls return its data.
IMD_CHANGE_METER_PATH = "/IMD/Dashboard/ChangeMeter"
IMD_PERIODS_PATH = "/IMD/Dashboard/GetCurrentAndPreviousPeriod"
# Selects the period type in the IMD session; kept when switching meters.
IMD_CHANGE_PERIOD_TYPE_PATH = "/IMD/Dashboard/ChangePeriodType"
PERIOD_TYPE_WEEK = "Vecka"  # current and previous week, per day
PERIOD_TYPE_YEAR = "År"  # current and previous year, per month

_LOGIN_CONTROL = "ctl00$ctl01$DefaultSiteContentPlaceHolder1$Col2$LoginControl1$"
FIELD_USERNAME = f"{_LOGIN_CONTROL}txtUserID"
FIELD_PASSWORD = f"{_LOGIN_CONTROL}txtPassword"
FIELD_LOGIN_BUTTON = f"{_LOGIN_CONTROL}btnLogin"

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)


class HyresbostaderError(Exception):
    """Base error for the Hyresbostäder client."""


class HyresbostaderConnectionError(HyresbostaderError):
    """The portal could not be reached or returned something unexpected."""


class HyresbostaderAuthError(HyresbostaderError):
    """The portal rejected the credentials."""


@dataclass(frozen=True)
class Meter:
    """A meter shown on the IMD dashboard, e.g. Kallvatten or Temperatur."""

    id: str
    name: str


@dataclass(frozen=True)
class PeriodValue:
    """One bar in the period chart (a day or a month). value is None if not reported."""

    label: str | None
    value: float | None


@dataclass(frozen=True)
class Period:
    """A week ("Vecka 41", per day) or a year ("2026", per month) of readings."""

    label: str
    start: datetime
    end: datetime
    total: float | None
    values: list[PeriodValue]

    def daily_values(self) -> list[tuple[date, float]]:
        """Reported (date, value) pairs of a week period, which has one value per day."""
        first_day = self.start.date()
        return [
            (first_day + timedelta(days=index), item.value)
            for index, item in enumerate(self.values)
            if item.value is not None
        ]


@dataclass(frozen=True)
class MeterData:
    """Latest reading and the current and previous week and year for one meter."""

    id: str
    name: str
    unit: str
    number: str
    last_count: float
    last_count_time: datetime
    current_week: Period
    previous_week: Period
    current_year: Period
    previous_year: Period

    @property
    def key(self) -> str:
        """Stable identifier: the meter number survives the portal renumbering its meter ids."""
        return self.number or self.id


# Readings are stamped at the end of the day they cover (23:59 or 00:00).
_END_OF_DAY_MARGIN = timedelta(hours=1)


def has_readings_until(meters: Iterable[MeterData], day_start: datetime) -> bool:
    """Return True if every meter with readings has one for the day before day_start.

    Meters without readings (e.g. an unused Sköljmedel) report the time of the
    request as their last reading, so times after day_start are ignored.
    """
    times = [m.last_count_time for m in meters if m.last_count_time <= day_start]
    return bool(times) and all(t >= day_start - _END_OF_DAY_MARGIN for t in times)


class HyresbostaderAPI:
    """Logs in to the Hyresbostäder portal and keeps the session cookies."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        base_url: str = BASE_URL,
        imd_base_url: str = IMD_BASE_URL,
    ) -> None:
        self._session = session
        self._base_url = base_url
        self._imd_base_url = imd_base_url
        self._username = username
        self._password = password
        # The selected meter lives in the server-side IMD session, so meter
        # requests must not interleave.
        self._imd_lock = asyncio.Lock()

    async def async_login(self) -> None:
        """Log in with username and password.

        Raises HyresbostaderAuthError on bad credentials and
        HyresbostaderConnectionError on network or parsing problems.
        """
        login_url = f"{self._base_url}{LOGIN_PATH}"
        try:
            async with self._session.get(
                login_url, timeout=REQUEST_TIMEOUT
            ) as response:
                response.raise_for_status()
                login_page = await response.text()

            form_data = _build_login_form(login_page, self._username, self._password)

            async with self._session.post(
                login_url, data=form_data, timeout=REQUEST_TIMEOUT
            ) as response:
                response.raise_for_status()
                result_page = await response.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HyresbostaderConnectionError(
                f"Error communicating with {self._base_url}: {err}"
            ) from err

        if _has_login_form(result_page):
            raise HyresbostaderAuthError("Invalid username or password")

        _LOGGER.debug("Logged in to %s", self._base_url)

    async def async_get_user_id(self) -> str:
        """Return the IMD userId for the logged-in user.

        Requires async_login() first. Raises HyresbostaderAuthError if the
        portal session is not logged in.
        """
        try:
            async with self._session.get(
                f"{self._base_url}{IMD_SUBMIT_PATH}",
                allow_redirects=False,
                timeout=REQUEST_TIMEOUT,
            ) as response:
                location = response.headers.get("Location", "")
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HyresbostaderConnectionError(
                f"Error communicating with {self._base_url}: {err}"
            ) from err

        # Not logged in redirects to /Global/Error.aspx instead of the IMD site.
        user_ids = parse_qs(urlparse(location).query).get("userId")
        if not user_ids:
            raise HyresbostaderAuthError("Not logged in, no userId in IMD redirect")
        return user_ids[0]

    async def async_get_meters(self, user_id: str) -> list[Meter]:
        """Return the meters listed on the IMD dashboard.

        The IMD site is authorized by userId alone, no portal login needed.
        """
        async with self._imd_lock:
            return await self._async_open_dashboard(user_id)

    async def async_get_meter_data(self, user_id: str) -> dict[str, MeterData]:
        """Return readings for all meters on the dashboard, keyed by meter id."""
        async with self._imd_lock:
            meters = await self._async_open_dashboard(user_id)
            meter_infos: dict[str, dict[str, Any]] = {}
            periods: dict[str, dict[str, dict[str, Any]]] = {}
            for period_type in (PERIOD_TYPE_WEEK, PERIOD_TYPE_YEAR):
                await self._async_change_period_type(user_id, period_type)
                for meter in meters:
                    meter_infos[meter.id] = await self._async_change_meter(
                        user_id, meter.id
                    )
                    periods.setdefault(meter.id, {})[period_type] = (
                        await self._async_imd_get_json(
                            IMD_PERIODS_PATH, {"userId": user_id}
                        )
                    )
            return {
                meter.id: _parse_meter_data(
                    meter_infos[meter.id],
                    periods[meter.id][PERIOD_TYPE_WEEK],
                    periods[meter.id][PERIOD_TYPE_YEAR],
                )
                for meter in meters
            }

    async def _async_change_meter(self, user_id: str, meter_id: str) -> dict[str, Any]:
        meter_info = await self._async_imd_get_json(
            IMD_CHANGE_METER_PATH, {"meterId": meter_id, "userId": user_id}
        )
        # A rejected meter keeps the previous one selected, so make sure
        # following period data belongs to this meter.
        if str(meter_info.get("id")) != meter_id:
            raise HyresbostaderConnectionError(f"IMD did not switch to meter {meter_id}")
        return meter_info

    async def _async_change_period_type(self, user_id: str, period_type: str) -> None:
        # The session may still have the type from an earlier refresh, so always set it.
        period = await self._async_imd_get_json(
            IMD_CHANGE_PERIOD_TYPE_PATH, {"periodName": period_type, "userId": user_id}
        )
        if period.get("type") != period_type:
            raise HyresbostaderConnectionError(
                f"IMD did not switch to period type {period_type}"
            )

    async def _async_open_dashboard(self, user_id: str) -> list[Meter]:
        """Load the dashboard, which also starts the IMD session the JSON endpoints need."""
        try:
            async with self._session.get(
                f"{self._imd_base_url}{IMD_DASHBOARD_PATH}",
                params={"userId": user_id},
                timeout=REQUEST_TIMEOUT,
            ) as response:
                response.raise_for_status()
                dashboard = await response.text()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HyresbostaderConnectionError(
                f"Error communicating with {self._imd_base_url}: {err}"
            ) from err

        return _parse_meters(dashboard)

    async def _async_imd_get_json(
        self, path: str, params: dict[str, str]
    ) -> dict[str, Any]:
        try:
            async with self._session.get(
                f"{self._imd_base_url}{path}", params=params, timeout=REQUEST_TIMEOUT
            ) as response:
                response.raise_for_status()
                data = await response.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise HyresbostaderConnectionError(
                f"Error fetching {path} from {self._imd_base_url}: {err}"
            ) from err
        if not isinstance(data, dict):
            raise HyresbostaderConnectionError(f"Unexpected response from {path}")
        return data


def _build_login_form(html: str, username: str, password: str) -> dict[str, str]:
    """Collect the ASP.NET form fields from the login page and add credentials."""
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form")
    if form is None or form.find("input", {"name": FIELD_PASSWORD}) is None:
        raise HyresbostaderConnectionError("Login form not found on login page")

    data: dict[str, str] = {}
    for field in form.find_all("input"):
        name = field.get("name")
        # Only hidden/text fields are posted; other buttons would trigger other actions.
        if name and field.get("type", "text") in ("hidden", "text"):
            data[name] = field.get("value", "")

    if "__VIEWSTATE" not in data:
        raise HyresbostaderConnectionError("__VIEWSTATE not found on login page")

    data[FIELD_USERNAME] = username
    data[FIELD_PASSWORD] = password
    data[FIELD_LOGIN_BUTTON] = "Logga in"
    return data


def _has_login_form(html: str) -> bool:
    """Return True if the page still shows the password field, i.e. login failed."""
    soup = BeautifulSoup(html, "html.parser")
    return soup.find("input", {"name": FIELD_PASSWORD}) is not None


def _parse_meters(html: str) -> list[Meter]:
    """Extract meters from the selector list on the IMD dashboard."""
    soup = BeautifulSoup(html, "html.parser")
    meters = [
        Meter(id=item["data-meter-id"], name=item.get_text(strip=True))
        for item in soup.select("ul.meters li.selector[data-meter-id]")
    ]
    if not meters:
        raise HyresbostaderConnectionError("No meters found on IMD dashboard")
    return meters


_DOTNET_DATE = re.compile(r"/Date\((-?\d+)([+-])(\d{2})(\d{2})\)/")


def _parse_dotnet_date(value: str) -> datetime:
    """Parse "/Date(1791151200000+0200)/" (epoch ms, display offset)."""
    match = _DOTNET_DATE.fullmatch(value)
    if match is None:
        raise ValueError(f"Invalid date: {value!r}")
    millis, sign, hours, minutes = match.groups()
    offset = timedelta(hours=int(hours), minutes=int(minutes))
    tz = timezone(offset if sign == "+" else -offset)
    return datetime.fromtimestamp(int(millis) / 1000, tz=tz)


def _parse_number(value: str | float | None) -> float | None:
    """Parse Swedish formatted numbers such as "21,46" or "1 234"."""
    if value is None or isinstance(value, (int, float)):
        return value
    cleaned = re.sub(r"\s", "", value).replace(",", ".")
    return float(cleaned) if cleaned else None


def _parse_period(period: dict[str, Any]) -> Period:
    return Period(
        label=period["periodLabel"],
        start=_parse_dotnet_date(period["start"]),
        end=_parse_dotnet_date(period["end"]),
        total=_parse_number(period["summary"]["value"]),
        values=[
            PeriodValue(label=item["labelLong"], value=_parse_number(item["value"]))
            for item in period["data"]
        ],
    )


def _parse_meter_data(
    meter_info: dict[str, Any], weeks: dict[str, Any], years: dict[str, Any]
) -> MeterData:
    try:
        return MeterData(
            id=str(meter_info["id"]),
            name=meter_info["name"],
            unit=meter_info["unit"],
            number=meter_info["number"],
            last_count=float(meter_info["lastCountNum"]),
            last_count_time=_parse_dotnet_date(meter_info["lastCountTime"]),
            current_week=_parse_period(weeks["currentPeriod"]),
            previous_week=_parse_period(weeks["previousPeriod"]),
            current_year=_parse_period(years["currentPeriod"]),
            previous_year=_parse_period(years["previousPeriod"]),
        )
    except (KeyError, TypeError, ValueError) as err:
        raise HyresbostaderConnectionError(f"Unexpected IMD meter data: {err}") from err
