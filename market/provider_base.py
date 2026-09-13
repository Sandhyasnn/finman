from typing import Dict, Iterable


class MarketDataProvider:
    """
    Base interface for market price providers.
    """

    def get_prices(self, instruments: Iterable[str]) -> Dict[str, float]:
        """
        Return a mapping: instrument_name -> current_price
        Missing instruments should simply be absent from the dict.
        """
        raise NotImplementedError
