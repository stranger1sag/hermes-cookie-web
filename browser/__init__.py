"""Browser connection utilities for cookie-web provider."""

from .chrome import ChromeManager
from .cdp_helpers import CDPHelper

__all__ = ["ChromeManager", "CDPHelper"]
