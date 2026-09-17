from typing import Dict, Iterable, Optional
import yfinance as yf
import re, math

from market.provider_base import MarketDataProvider
from logger import logger

_STOCK_SYMBOL_RE = re.compile(r"^[A-Z0-9]+(-[A-Z0-9]+)*$")

class YahooPriceProvider(MarketDataProvider):
    """
    Yahoo Finance price provider.

    Retrieves recent daily prices for securities using their Yahoo Finance symbols.
    NSE securities normally use the standard '<symbol>.NS' format.

    Some NSE securities are listed by Yahoo under an RR-series symbol ('<symbol>-RR.NS') instead. If the standard 
    symbol has no usable price, get_prices() automatically retries using the RR-series symbol.    
    """
    def get_prices(self, instruments: Iterable[str]) -> Dict[str, float]:
        """
        Return the latest available closing price for each instrument.

        Instruments are expected to be Yahoo Finance symbols. Missing or
        unavailable symbols are omitted from the returned dictionary.

        For NSE symbols, an '<symbol>-RR.NS' fallback is attempted when the standard '<symbol>.NS' symbol does not 
        provide a usable price. The returned dictionary always uses the originally requested symbol as its key.
        """        
        prices: Dict[str, float] = {}

        symbols = list(instruments)
        if not symbols:
            return prices

        try:
            # Fetch all requested symbols in a single Yahoo request.
            data = yf.download(
                tickers=" ".join(symbols),
                period="5d",
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                threads=True,
                progress=False
            )

            for symbol in symbols:
                try:
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
                            continue

                except Exception:
                    # Standard Yahoo symbol unavailable; try the RR-series fallback below.
                    pass

                # Some NSE securities use Yahoo's RR-series symbol instead of the
                # standard .NS symbol. Try that symbol only when the standard lookup failed.
                if symbol.endswith(".NS") and not symbol[:-3].endswith("-RR"):
                    fallback_symbol = f"{symbol[:-3]}-RR.NS"
                    # Keep the original symbol as the price dictionary key so callers
                    # do not need to know which Yahoo symbol supplied the price.
                    try:
                        fallback_data = yf.download(
                            tickers=fallback_symbol,
                            period="5d",
                            interval="1d",
                            group_by="ticker",
                            auto_adjust=False,
                            threads=False,
                            progress=False
                        )

                        close = fallback_data[fallback_symbol]["Close"].dropna()

                        if not close.empty:
                            price = float(close.iloc[-1])
                            if math.isfinite(price):
                                prices[symbol] = price
                                logger.info("Yahoo RR fallback: %s -> %s", symbol, fallback_symbol)

                    except Exception:
                        # RR-series symbol also unavailable; leave this security unpriced.
                        pass

        except Exception:
            # Batch Yahoo request failed; return any prices obtained so far.
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

