from typing import Dict, Iterable
import requests
import math

from market.provider_base import MarketDataProvider

import logging
logger = logging.getLogger(__name__)

class AMFINavProvider(MarketDataProvider):
    """
    Indian Mutual Fund NAV provider using AMFI data.

    Resilience features:
    - Primary + fallback NAV endpoints
    - Explicit logging on failures
    - Silent return contract preserved (returns {} on failure)
    """

    PRIMARY_NAV_URL = "https://www.amfiindia.com/spages/NAVAll.txt"
    FALLBACK_NAV_URL = "https://portal.amfiindia.com/spages/NAVAll.txt"

    REQUEST_TIMEOUT = 10

    def get_prices(self, instruments: Dict[str, str]) -> Dict[str, float]:
        prices: Dict[str, float] = {}

        if not instruments:
            return prices

        wanted = {str(code): name for name,code in instruments.items()}

        # Try primary, then fallback
        for idx, url in enumerate(
            (self.PRIMARY_NAV_URL, self.FALLBACK_NAV_URL),
            start=1,
        ):
            try:
                resp = requests.get(url, timeout=self.REQUEST_TIMEOUT)
                resp.raise_for_status()

                if idx == 2:
                    logger.info("AMFI primary endpoint failed; using fallback endpoint")

                self._parse_nav_text(resp.text, wanted, prices)
                return prices

            except Exception as exc:
                if idx == 1:
                    logger.error("Failed to fetch AMFI NAV data from %s: %s", url, exc,)
                else:
                    logger.critical("Failed to fetch AMFI NAV data from %s: %s", url, exc,)

        # Both endpoints failed
        logger.error("AMFI NAV fetch failed from all endpoints; MF prices unavailable")
        return prices

    @staticmethod
    def _parse_nav_text(text: str, wanted: Dict[str, str], prices: Dict[str, float]) -> None:
        """
        Parse AMFI NAVAll.txt content and populate prices in-place.
        """
        for line in text.splitlines():
            if ";" not in line:
                continue

            parts = line.split(";")
            if len(parts) < 8:
                continue

            scheme_code = parts[0].strip()
            nav_str = parts[6].strip()

            if scheme_code not in wanted or not nav_str:
                continue

            try:
                prices[wanted[scheme_code]] = float(nav_str)
                price = float(nav_str)
                # When AMFI returns historical price data, a requested security might have no usable price for the
                # latest row. It is seen to return NaN that will eventually fail. 
                # The below condition checks if the floating number is "a real, finite numeric value?"
                if math.isfinite(price):
                    prices[wanted[scheme_code]] = price
            except ValueError:
                continue

