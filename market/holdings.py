"""
Identify securities that currently have open positions
and therefore require a market price.
"""

from ingestion.models import Transaction
from market.yahoo_provider import normalize_yahoo_symbol

YAHOO_ASSET_TYPES = {"Stock", "ETF", "SMALLCASE"}
YAHOO_ASSET_TYPES_US = {"US STOCK"}
AMFI_ASSET_TYPES = {"MF(Equity)", "MF(Debt)"}

def get_price_securities(transactions: list[Transaction], ) -> dict[str, list[str, str]]:
    """
    Return unique securities with open positions, grouped by price provider.

    A transaction is considered open when position_closed is blank or None.
    Closed and closed-out positions are ignored.

    Returns:
        {
            "yahoo": {[HDFCBANK: HDFCBANK.NS, ONGC: ONGC.NS, MSFT: MSFT]},
            "amfi": [...]
        }

    Raises:
        ValueError: If an open position contains an unsupported asset type.
    """
    yahoo = {}
    amfi = set()

    for transaction in transactions:
        if transaction.position_closed:
            continue

        if transaction.asset_type in YAHOO_ASSET_TYPES:
            symbol = normalize_yahoo_symbol(transaction.security) # Add .NS to the symbol or something like that
            if symbol:
                yahoo[transaction.security] = symbol
        elif transaction.asset_type in YAHOO_ASSET_TYPES_US:
            yahoo[transaction.security] = transaction.security.upper() # Take the symbol as is
        elif transaction.asset_type in AMFI_ASSET_TYPES:
            amfi.add(transaction.security)
        else:
            raise ValueError(
                f"Unsupported asset type '{transaction.asset_type}' "
                f"for open security '{transaction.security}'"
            )

    return {
        "yahoo": yahoo,
        "amfi": {name: name for name in sorted(amfi)},
    }

