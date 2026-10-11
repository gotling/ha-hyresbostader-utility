"""Manually verify login, userId, meters and meter data against the live portal.

Usage:
    HB_USERNAME=19800101-1234 HB_PASSWORD=secret uv run scripts/try_login.py
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custom_components.hyresbostader_utility.api import (  # noqa: E402
    HyresbostaderAPI,
    HyresbostaderError,
)


async def main() -> int:
    logging.basicConfig(level=logging.DEBUG)
    username = os.environ["HB_USERNAME"]
    password = os.environ["HB_PASSWORD"]

    async with aiohttp.ClientSession() as session:
        api = HyresbostaderAPI(session, username, password)
        try:
            await api.async_login()
            print("Login OK. Cookies:", sorted(c.key for c in session.cookie_jar))
            user_id = await api.async_get_user_id()
            print("userId:", user_id)
            for meter in await api.async_get_meters(user_id):
                print(f"  meter {meter.id}: {meter.name}")
            for data in (await api.async_get_meter_data(user_id)).values():
                print(
                    f"{data.name} ({data.id}): last {data.last_count} {data.unit}"
                    f" at {data.last_count_time:%Y-%m-%d %H:%M}"
                )
                for period in (
                    data.current_week,
                    data.previous_week,
                    data.current_year,
                    data.previous_year,
                ):
                    values = ", ".join(str(item.value) for item in period.values)
                    print(f"  {period.label}: total {period.total} [{values}]")
        except HyresbostaderError as err:
            print(f"Failed: {type(err).__name__}: {err}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
