"""
Refresh current market prices and write the price snapshot.
"""
from ast import If
import json
from pathlib import Path
from datetime import datetime, timezone

from ingestion.models import Transaction
from market.holdings import get_price_securities, YAHOO_ASSET_TYPES_US
from market.yahoo_provider import YahooPriceProvider
from market.amfi_provider import AMFINavProvider
from market.external_links import load_mf_master
from logger import logger

def _load_snapshot(path: str | Path) -> dict:
    path = Path(path)

    if not path.exists():
        return {"updated_at": None, "prices": {}, "stale": []}

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Unable to read previous price snapshot: %s", path)
        return {"updated_at": None, "prices": {}, "stale": []}
    
def _merge_previous_prices(prices: dict[str, float], previous_prices: dict[str, float], securities: list[str],) -> tuple[dict[str, float], list[str], list[str]]:
    missing = sorted(set(securities) - set(prices))
    stale = []

    for security in missing:
        if security in previous_prices:
            prices[security] = previous_prices[security]
            stale.append(security)

    missing = sorted(set(missing) - set(stale))

    return prices, stale, missing

def _get_current_price(transaction: Transaction, price: float, usd_inr_rate: float | None,) -> float | None:
    """Return the current price in INR for the transaction, converting from USD if necessary."""
    if transaction.asset_type in YAHOO_ASSET_TYPES_US:
        if usd_inr_rate is None:
            return None
        return price * usd_inr_rate

    return price

def _update_transaction_prices(transactions: list[Transaction], securities: dict[str, dict[str, str]], 
                               prices: dict[str, float], usd_inr_rate: float | None = None,) -> dict:
    """Update current market price and value for open transactions."""
    yahoo = securities["yahoo"]
    amfi = securities["amfi"]

    # count of the transactions for various statuses
    updated = 0   # Current transactions getting updated
    skipped_closed = 0 # Closed transaction getting skipped
    skipped_unmapped = 0  # prices for these stock was not found in the price_snapshot
    skipped_no_price = 0  #prices for these stocks don't have a price in the price_snapshot or is none

    for transaction in transactions:
        if transaction.position_closed:
            skipped_closed += 1
            continue

        if transaction.security in yahoo:
            price_key = yahoo[transaction.security]
        elif transaction.security in amfi:
            price_key = amfi[transaction.security]
        else:
            skipped_unmapped += 1
            continue           

        price = prices.get(price_key)

        if price is not None:
            current_price = _get_current_price(transaction, price, usd_inr_rate)            
            if current_price is None:
                transaction.current_price = None
                transaction.current_value = None
                skipped_no_price += 1
                continue

            transaction.current_price = current_price
            transaction.current_value = current_price * transaction.transaction_quantity
            updated += 1

        else:
            skipped_no_price += 1

    return {
        "transactions_processed": len(transactions),
        "transactions_enriched": updated,
        "transactions_skipped_closed": skipped_closed,
        "transactions_skipped_unmapped": skipped_unmapped,
        "transactions_skipped_no_price": skipped_no_price,
    }

def _needs_usd_inr_rate(transactions: list[Transaction]) -> bool:
    return any(
        t.asset_type in YAHOO_ASSET_TYPES_US
        and not t.position_closed
        for t in transactions
    )

def refresh_prices(transactions: list[Transaction], output_file: str | Path) -> dict:
    # Get the list of current/open stocks and MFs from current trasnactions 
    securities = get_price_securities(transactions)

    # Get the current prices from the existing json 
    previous = _load_snapshot(output_file)
    previous_prices = previous.get("prices", {})

    logger.info(f"Processing: {len(securities["yahoo"])} Stocks, {len(securities["amfi"])} Mutual Funds.")

    prices = {}
    yahoo_prices = {}
    mf_prices = {}
    enrichment_status = [] # Status of the transaction enrishment process

    # yahoo_prices.update(YahooPriceProvider().get_prices(list(securities["yahoo"].values()) ) )
    yahoo_symbols = list(securities["yahoo"].values())

    # Get the stocks prices from yahoo provider
    yahoo_prices.update(YahooPriceProvider().get_prices(yahoo_symbols))

    # Get USD/INR separately when US stocks are present
    usd_inr_rate = None

    # Maintain the status of the FX rate retrieval process
    fx_required = _needs_usd_inr_rate(transactions)
    fx_status = "not_required"
    stale_exchange_rates = []

    usd_inr_rate = None

    if fx_required:
        fx_prices = YahooPriceProvider().get_prices(["INR=X"])  # Fresh/current prices
        usd_inr_rate = fx_prices.get("INR=X")

        if usd_inr_rate is not None:
            fx_status = "current"
        else:
            # If current FX is unavailable, use the previous rate if available.
            previous_exchange_rates = previous.get("exchange_rates", {})
            previous_usd_inr = previous_exchange_rates.get("USDINR")

            if previous_usd_inr is not None:
                usd_inr_rate = float(previous_usd_inr)
                fx_status = "stale"
                stale_exchange_rates.append("USDINR")
                logger.warning("Unable to get current USD/INR rate; using previous rate: %.6f", usd_inr_rate)
            else:
                fx_status = "unavailable"
                logger.warning("Unable to get USD/INR rate and no previous rate is available.")

    prices.update(yahoo_prices)

    # Get the MF name-amgi_code map for the mf master json
    master = load_mf_master()

    mf_code_map = {}
    for name in securities["amfi"]:
        entry = master.get(name)

        if not entry or not entry.get("amfi_code"):
            # The name in the current transaction does not have an entry in the mf master json
            logger.warning(f"MF missing AMFI mapping: {name}")
            continue

        mf_code_map[name] = str(entry["amfi_code"])  # updat the amfi code against each MF name

    # Get the prices for each MF baaed on the amfi code form the amfi provider
    mf_prices.update(AMFINavProvider().get_prices(mf_code_map))
    prices.update(mf_prices)

    # If some of the current prices are missing extract from the existing json and 
    # merge inot the current prices
    all_securities =  list(securities["yahoo"].values()) + list(securities["amfi"].values())
    prices, stale, missing = _merge_previous_prices(prices, previous_prices,all_securities)
    # enrichment_status = _update_transaction_prices(transactions, securities, prices)
    enrichment_status = _update_transaction_prices(transactions, securities, prices, usd_inr_rate)
    
    # Update the missing data to report back out   
    updated_at = datetime.now(timezone.utc).astimezone().isoformat()

    yahoo_keys = list(securities["yahoo"].values())
    amfi_keys = list(securities["amfi"].values())
    stock_missing = sorted(set(yahoo_keys) & set(missing))
    mf_missing = sorted(set(amfi_keys) & set(missing))
    stock_stale = sorted(set(yahoo_keys) & set(stale))
    mf_stale = sorted(set(amfi_keys) & set(stale))

    # updatA the date and time when theprices were obtained
    snapshot = {"updated_at": updated_at, "prices": prices, 
                    "exchange_rates": {"USDINR": usd_inr_rate},
                "stale": stale, "stale_exchange_rates": stale_exchange_rates,}

    # Write the prices back to the price snapshot json for persistence
    Path(output_file).write_text(
        json.dumps(snapshot, indent=2),
        encoding="utf-8",
    )

    logger.info(f"Got Prices: {len(yahoo_prices)} Stocks, "f"{len(mf_prices)} Mutual Funds. "f"USD/INR: {usd_inr_rate:.2f} ({fx_status}).")
    return {
        "updated_at": datetime.fromisoformat(updated_at).strftime("%d-%b-%y,%H:%M"),
        "stocks": {
            "requested": len(securities["yahoo"]),
            "updated": len(yahoo_prices),
            "missing": stock_missing,
            "stale": stock_stale
        },
        "mutual_funds": {
            "requested": len(securities["amfi"]),
            "mapped": len(mf_code_map),
            "updated": len(mf_prices),
            "missing": mf_missing,
            "stale": mf_stale
        },
        "exchange_rates": {
            "USDINR": usd_inr_rate,
            "status": fx_status,
            "required": fx_required,
        },        
        "transaction_enrichment": enrichment_status,
        "output_file": output_file,
        "prices": snapshot,
    }
