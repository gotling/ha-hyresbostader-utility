"""Constants for the Hyresbostäder Utility integration."""

from datetime import timedelta

DOMAIN = "hyresbostader_utility"
MANUFACTURER = "Hyresbostäder"

# IMD userId discovered at login; the IMD site is authorized by it alone.
CONF_USER_ID = "user_id"

# Meters are reported about once a day, so hourly polling is plenty.
UPDATE_INTERVAL = timedelta(hours=1)

UNIT_LITER = "Liter"
UNIT_CELSIUS = "°C"
