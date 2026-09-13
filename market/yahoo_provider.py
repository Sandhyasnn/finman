from typing import Dict, Iterable, Optional
import yfinance as yf
import re, math

from market.provider_base import MarketDataProvider

_STOCK_SYMBOL_RE = re.compile(r"^[A-Z0-9]+(-[A-Z0-9]+)*$")

class YahooPriceProvider(MarketDataProvider):
    """
    Yahoo Finance price provider.
    """

    def get_prices(self, instruments: Iterable[str]) -> Dict[str, float]:
        prices: Dict[str, float] = {}

        symbols = list(instruments)
        if not symbols:
            return prices

        try:
            # Batch fetch
            data = yf.download(
                tickers=" ".join(symbols),
                period="5d",
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                threads=True,
                progress=False
            )

            from logger import logger
            for symbol in symbols:
                try:
                    # Multi-symbol vs single-symbol handling
                    close = data[symbol]["Close"]

                    # Use the most recent valid closing price. Removes the NaN values from the series
                    valid_close = close.dropna()

                    if not valid_close.empty:
                        price = float(valid_close.iloc[-1])
                        # When Yahoo returns historical price data, a requested security might have no usable price for the
                        # latest row. It is seen to return NaN that will eventually fail. 
                        # The below condition checks if the floating number is "a real, finite numeric value?"
                        if math.isfinite(price): 
                            prices[symbol] = price

                except Exception:
                    # Missing symbol / malformed data
                    continue

        except Exception:
            # Network / Yahoo failure → return partial or empty
            return prices

        return prices
    
def normalize_yahoo_symbol(name: str) -> Optional[str]:
    if not name:
        return None

    name = name.strip().upper()

    # Reject spaces (MF names usually have spaces)
    if " " in name:
        return None

    # Allow: ABC, ABC-RE, ABC-P1, ABC-XYZ-1
    if not _STOCK_SYMBOL_RE.match(name):
        return None

    return f"{name}.NS"

