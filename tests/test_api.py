"""Tests for the Hyresbostäder API client."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from custom_components.hyresbostader_utility.api import (
    FIELD_LOGIN_BUTTON,
    FIELD_PASSWORD,
    FIELD_USERNAME,
    HyresbostaderAPI,
    HyresbostaderAuthError,
    HyresbostaderConnectionError,
    Meter,
)

FIXTURES = Path(__file__).parent / "fixtures"
LOGIN_PAGE = (FIXTURES / "login_page.html").read_text()
LOGIN_FAILED_PAGE = (FIXTURES / "login_failed.html").read_text()
LOGGED_IN_PAGE = "<html><body><h1>Min förbrukning</h1></body></html>"
IMD_DASHBOARD_PAGE = (FIXTURES / "imd_dashboard.html").read_text()
# Real ChangeMeter and GetCurrentAndPreviousPeriod (per period type) responses.
METER_FIXTURES = {
    meter_id: {
        "meter": json.loads((FIXTURES / f"change_meter_{meter_id}.json").read_text()),
        "Vecka": json.loads((FIXTURES / f"week_{meter_id}.json").read_text()),
        "År": json.loads((FIXTURES / f"year_{meter_id}.json").read_text()),
    }
    for meter_id in ("1001", "1005")
}
TWO_METER_DASHBOARD = """<ul class="meters">
<li class="selector active" data-meter-id="1001"><a href="#">Kallvatten</a></li>
<li class="selector" data-meter-id="1005"><a href="#">Temperatur</a></li>
</ul>"""
CEST = timezone(timedelta(hours=2))
CET = timezone(timedelta(hours=1))

USERNAME = "19800101-1234"
PASSWORD = "secret"
USER_ID = "TestUserId1="


class FakePortal:
    """Minimal stand-in for the ASP.NET portal and the IMD site."""

    def __init__(self) -> None:
        self.login_page = LOGIN_PAGE
        self.imd_dashboard_page = IMD_DASHBOARD_PAGE
        self.meter_fixtures = dict(METER_FIXTURES)
        self.selected_meter: str | None = None
        # Left over from an earlier refresh; the client must switch it explicitly.
        self.period_type = "År"
        self.get_status = 200
        self.posted_forms: list[dict[str, str]] = []
        self.cookie_on_post = False

    async def login_get(self, request: web.Request) -> web.Response:
        response = web.Response(
            status=self.get_status, text=self.login_page, content_type="text/html"
        )
        response.set_cookie("ASP.NET_SessionId", "abc123")
        return response

    async def login_post(self, request: web.Request) -> web.Response:
        form = dict(await request.post())
        self.posted_forms.append(form)
        self.cookie_on_post = request.cookies.get("ASP.NET_SessionId") == "abc123"
        if form.get(FIELD_USERNAME) == USERNAME and form.get(FIELD_PASSWORD) == PASSWORD:
            response = web.HTTPFound("/minforbrukning")
            response.set_cookie("PORTAL", "logged-in")
            raise response
        return web.Response(text=LOGIN_FAILED_PAGE, content_type="text/html")

    async def consumption(self, request: web.Request) -> web.Response:
        return web.Response(text=LOGGED_IN_PAGE, content_type="text/html")

    async def imd_submit(self, request: web.Request) -> web.Response:
        if request.cookies.get("PORTAL") != "logged-in":
            raise web.HTTPFound("/Global/Error.aspx?aspxerrorpath=/IMD_Submit.aspx")
        raise web.HTTPFound(f"{request.url.origin()}/IMD/?userId=TestUserId1%3d")

    async def imd_dashboard(self, request: web.Request) -> web.Response:
        if request.query.get("userId") != USER_ID:
            raise web.HTTPInternalServerError()
        response = web.Response(text=self.imd_dashboard_page, content_type="text/html")
        response.set_cookie("IMDSession", "imd123")
        return response

    def _check_imd_session(self, request: web.Request) -> None:
        # The real site answers 500 until the dashboard has been opened.
        if request.cookies.get("IMDSession") != "imd123":
            raise web.HTTPInternalServerError()
        if request.query.get("userId") != USER_ID:
            raise web.HTTPInternalServerError()

    async def change_meter(self, request: web.Request) -> web.Response:
        self._check_imd_session(request)
        meter_id = request.query.get("meterId")
        if meter_id not in self.meter_fixtures:
            raise web.HTTPInternalServerError()
        self.selected_meter = meter_id
        return web.json_response(self.meter_fixtures[meter_id]["meter"])

    async def change_period_type(self, request: web.Request) -> web.Response:
        self._check_imd_session(request)
        period_type = request.query.get("periodName")
        if period_type not in ("Vecka", "År"):
            raise web.HTTPInternalServerError()
        self.period_type = period_type
        # The real endpoint answers with the current period of the selected meter.
        meter_id = self.selected_meter or "1001"
        return web.json_response(
            self.meter_fixtures[meter_id][period_type]["currentPeriod"]
        )

    async def periods(self, request: web.Request) -> web.Response:
        self._check_imd_session(request)
        return web.json_response(
            self.meter_fixtures[self.selected_meter][self.period_type]
        )


@pytest.fixture
def portal() -> FakePortal:
    return FakePortal()


@pytest.fixture
async def base_url(portal):
    app = web.Application()
    app.router.add_get("/User/MyPagesLogin.aspx", portal.login_get)
    app.router.add_post("/User/MyPagesLogin.aspx", portal.login_post)
    app.router.add_get("/minforbrukning", portal.consumption)
    app.router.add_get(
        "/Res/Themes/HnabPortal3/Pages/HSO/Energi/IMD_Submit.aspx", portal.imd_submit
    )
    app.router.add_get("/IMD/", portal.imd_dashboard)
    app.router.add_get("/IMD/Dashboard/ChangeMeter", portal.change_meter)
    app.router.add_get("/IMD/Dashboard/ChangePeriodType", portal.change_period_type)
    app.router.add_get(
        "/IMD/Dashboard/GetCurrentAndPreviousPeriod", portal.periods
    )
    async with TestServer(app) as server:
        yield str(server.make_url("")).rstrip("/")


@pytest.fixture
async def session():
    # unsafe=True lets the cookie jar accept cookies from the 127.0.0.1 test server.
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
        yield session


async def test_login_success(session, base_url, portal):
    api = HyresbostaderAPI(session, USERNAME, PASSWORD, base_url=base_url)

    await api.async_login()

    assert len(portal.posted_forms) == 1
    assert portal.cookie_on_post


async def test_login_posts_credentials_and_aspnet_fields(session, base_url, portal):
    await HyresbostaderAPI(session, USERNAME, PASSWORD, base_url=base_url).async_login()

    form = portal.posted_forms[0]
    assert form[FIELD_USERNAME] == USERNAME
    assert form[FIELD_PASSWORD] == PASSWORD
    assert form[FIELD_LOGIN_BUTTON] == "Logga in"
    assert form["__VIEWSTATE"] == "TestViewState"
    assert form["__VIEWSTATEGENERATOR"] == "7B3D0533"
    assert form["__EVENTVALIDATION"] == "TestEventValidation"
    # Other buttons (search, BankID) must not be posted.
    assert "ctl00$ctl01$SearchSimple$btnSearch2" not in form
    assert not any("BankId" in key for key in form)


async def test_login_invalid_credentials(session, base_url):
    api = HyresbostaderAPI(session, USERNAME, "wrong", base_url=base_url)

    with pytest.raises(HyresbostaderAuthError):
        await api.async_login()


async def test_login_page_without_form(session, base_url, portal):
    portal.login_page = "<html><body>Maintenance</body></html>"
    api = HyresbostaderAPI(session, USERNAME, PASSWORD, base_url=base_url)

    with pytest.raises(HyresbostaderConnectionError):
        await api.async_login()
    assert portal.posted_forms == []


async def test_login_page_server_error(session, base_url, portal):
    portal.get_status = 503
    api = HyresbostaderAPI(session, USERNAME, PASSWORD, base_url=base_url)

    with pytest.raises(HyresbostaderConnectionError):
        await api.async_login()


async def test_login_unreachable_host(session):
    api = HyresbostaderAPI(session, USERNAME, PASSWORD, base_url="http://127.0.0.1:1")

    with pytest.raises(HyresbostaderConnectionError):
        await api.async_login()


def _api(session, base_url) -> HyresbostaderAPI:
    return HyresbostaderAPI(
        session, USERNAME, PASSWORD, base_url=base_url, imd_base_url=base_url
    )


async def test_get_user_id(session, base_url):
    api = _api(session, base_url)
    await api.async_login()

    assert await api.async_get_user_id() == USER_ID


async def test_get_user_id_not_logged_in(session, base_url):
    with pytest.raises(HyresbostaderAuthError):
        await _api(session, base_url).async_get_user_id()


async def test_get_meters(session, base_url):
    meters = await _api(session, base_url).async_get_meters(USER_ID)

    assert meters == [
        Meter(id="1001", name="Kallvatten"),
        Meter(id="1002", name="Varmvatten"),
        Meter(id="1003", name="Tvättar"),
        Meter(id="1004", name="Sköljmedel"),
        Meter(id="1005", name="Temperatur"),
    ]


async def test_get_meters_unknown_user_id(session, base_url):
    with pytest.raises(HyresbostaderConnectionError):
        await _api(session, base_url).async_get_meters("bogus")


async def test_get_meters_page_without_meters(session, base_url, portal):
    portal.imd_dashboard_page = "<html><body>Fel</body></html>"

    with pytest.raises(HyresbostaderConnectionError):
        await _api(session, base_url).async_get_meters(USER_ID)


async def test_get_meter_data(session, base_url, portal):
    portal.imd_dashboard_page = TWO_METER_DASHBOARD

    data = await _api(session, base_url).async_get_meter_data(USER_ID)

    assert list(data) == ["1001", "1005"]
    cold = data["1001"]
    assert cold.name == "Kallvatten"
    assert cold.unit == "Liter"
    assert cold.number == "000000-0000:KV:A1"
    assert cold.last_count == 465.845
    assert cold.last_count_time == datetime(2026, 10, 10, 23, 59, tzinfo=CEST)
    assert cold.current_week.label == "Vecka 41"
    assert cold.current_week.start == datetime(2026, 10, 5, tzinfo=CEST)
    assert cold.current_week.end == datetime(2026, 10, 11, tzinfo=CEST)
    assert cold.current_week.total == 594
    assert [v.value for v in cold.current_week.values] == [
        167, 86, 137, 153, 51, 0, None
    ]
    assert cold.current_week.values[0].label == "Måndag 5/10"
    assert cold.previous_week.label == "Vecka 40"
    assert cold.previous_week.total == 751


async def test_get_meter_data_year(session, base_url, portal):
    portal.imd_dashboard_page = TWO_METER_DASHBOARD

    cold = (await _api(session, base_url).async_get_meter_data(USER_ID))["1001"]

    assert cold.current_year.label == "2026"
    assert cold.current_year.start == datetime(2026, 1, 1, tzinfo=CET)
    assert cold.current_year.total == 5983
    assert len(cold.current_year.values) == 12
    assert cold.current_year.values[7].label == "Augusti"
    assert cold.current_year.values[7].value == 2140
    assert cold.current_year.values[8].value == 2763
    assert cold.previous_year.label == "2025"
    assert cold.previous_year.total == 0
    # Week data must still come from the week view, not the year left in the session.
    assert cold.current_week.label == "Vecka 41"


async def test_get_meter_data_decimal_comma(session, base_url, portal):
    portal.imd_dashboard_page = TWO_METER_DASHBOARD

    temperature = (await _api(session, base_url).async_get_meter_data(USER_ID))[
        "1005"
    ]

    assert temperature.unit == "°C"
    assert temperature.last_count == 21.29
    assert temperature.current_week.total == 21.46
    assert temperature.current_week.values[0].value == 20.79
    assert temperature.previous_week.total == 20.74


async def test_get_meter_data_rejected_meter(session, base_url, portal):
    # The full dashboard lists meters the fake has no data for -> ChangeMeter 500.
    with pytest.raises(HyresbostaderConnectionError):
        await _api(session, base_url).async_get_meter_data(USER_ID)


async def test_get_meter_data_meter_mismatch(session, base_url, portal):
    portal.imd_dashboard_page = TWO_METER_DASHBOARD
    portal.meter_fixtures["1005"] = METER_FIXTURES["1001"]

    with pytest.raises(HyresbostaderConnectionError):
        await _api(session, base_url).async_get_meter_data(USER_ID)


async def test_get_meter_data_unexpected_json(session, base_url, portal):
    portal.imd_dashboard_page = TWO_METER_DASHBOARD
    portal.meter_fixtures["1001"] = {**METER_FIXTURES["1001"], "Vecka": {"currentPeriod": {}}}

    with pytest.raises(HyresbostaderConnectionError):
        await _api(session, base_url).async_get_meter_data(USER_ID)
