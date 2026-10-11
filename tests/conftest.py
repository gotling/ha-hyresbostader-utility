"""Let the API tests import the integration package without Home Assistant installed."""

import sys
import types
from pathlib import Path

_PACKAGE = "custom_components.hyresbostader_utility"
_PACKAGE_DIR = Path(__file__).parent.parent / "custom_components" / "hyresbostader_utility"

# Register the package without running its __init__.py, which imports homeassistant.
if _PACKAGE not in sys.modules:
    import custom_components

    package = types.ModuleType(_PACKAGE)
    package.__path__ = [str(_PACKAGE_DIR)]
    sys.modules[_PACKAGE] = package
    custom_components.hyresbostader_utility = package
