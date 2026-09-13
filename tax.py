"""
FinMan Tax View Builders
========================

Builds the Tax Portfolio, Tax Broker and Tax Detail views
from normalized TaxTransaction records.

Tax Portfolio:
    grouped by Asset Type + Owner

Tax Broker:
    grouped by Broker + Owner

Tax Detail:
    filtered by FY + Owner + Asset Type + Broker + Security
"""

from collections import defaultdict

from ingestion.models import (
    TaxTransaction,
    TaxSummary,
    TaxSection,
    TaxView,
)


# ------------------------------------------------------------------
# Tax filtering
# ------------------------------------------------------------------
def _filter_tax_transactions(tax_transactions: list[TaxTransaction], fy: str, owner: str | None = None) -> list[TaxTransaction]:
    """
    Return tax transactions matching the requested FY and owner.

    owner=None means all owners.
    """
    return [
        transaction
        for transaction in tax_transactions
        if transaction.fy == fy
        and (owner is None or transaction.owner == owner)
    ]

# ------------------------------------------------------------------
# Tax summaries
# ------------------------------------------------------------------
def _build_tax_summary(tax_transactions: list[TaxTransaction]) -> list[TaxSummary]:
    """
    Aggregate tax transactions by gain type i.e long-term/short-term or LTCG/STCG

    Returns one summary row for each gain type, for example:
        Long Term
        Short Term
    """
    # whenver grouped is called, defualtdict ensures that the three set of values are initialized and ready
    # lambda means the above happens whenver grouped is called with a parameter that is appearing for the first time
    # So for our case the below ends up creating two sets, one for short term and another for long term
    grouped = defaultdict(
        lambda: {
            "buy_value": 0,
            "sell_value": 0,
            "capital_gain": 0,
        }
    )

    for transaction in tax_transactions:
        # The 'values' is for grouping. The key here is the gain_type i.e. long-term/short-term
        # We are summing over the gain_type
        values = grouped[transaction.gain_type]

        values["buy_value"] += transaction.buy_value
        values["sell_value"] += transaction.sell_value
        values["capital_gain"] += transaction.capital_gain

    # The 'grouped' dictionary is converted back to the TaxSummary model before being returned
    return [
        TaxSummary(
            gain_type=gain_type,
            buy_value=values["buy_value"],
            sell_value=values["sell_value"],
            capital_gain=values["capital_gain"],
        )
        for gain_type, values in sorted(grouped.items())
    ]

def _aggregate_tax_transactions(tax_transactions: list[TaxTransaction]) -> list[TaxTransaction]:
    """
    Aggregate tax transactions for display.

    Groups by Security + Gain Type + Broker so that each displayed row remains clickable to the correct tax holding.

    The underlying tax transactions are not modified.
    """
    grouped = {}   # Use and return a new object

    for transaction in tax_transactions:
        # The key is for grouping. For the view we are planning the basic grouping unit is 
        # the security-long term/short-term-broker unit
        key = (transaction.security, transaction.gain_type, transaction.broker)

        if key not in grouped:
            # If it the first time you ar encountering this key then copy the row
            # Also, we are doing a model copy / deep copy (deep=true) since we want a new object, 
            # not modify the original one app.state.tax
            grouped[key] = transaction.model_copy(deep=True)
        else:
            # If the key is repeating then sum the non key columns, in this case - buy value, sell value and CG
            grouped[key].buy_value += transaction.buy_value
            grouped[key].sell_value += transaction.sell_value
            grouped[key].capital_gain += transaction.capital_gain

    return list(grouped.values())

# ------------------------------------------------------------------
# Tax Portfolio view
# ------------------------------------------------------------------
def build_tax_portfolio_view(tax_transactions: list[TaxTransaction], fy: str, owner: str | None = None) -> TaxView:
    """
    Build the Tax Portfolio view.

    Sections are grouped by:
        Asset Type + Owner

    The original TaxTransaction set is not muted and copies are used for mutating here.

    1. Filter the input TaxTransaction records by the provided FY and Owner. Owner=None means all owners.
    2. From the filtered records, identify the unique Asset Type + Owner combinations. Each combination becomes one section.
    3. For each Asset Type + Owner combination:
       - Select the transactions belonging to that section from the filterd records.
       - Aggregate them by Security + Gain Type + Broker.Sum the applicable monetary fields within each group.
       - This produces one summary row per Security + Gain Type + Broker.
    4. Create a TaxSection using the aggregated rows and calculate the section-level tax summary by gain type.
      The TaxSection contains everything the UI needs to render one Asset Type + Owner section.
    5. Append each TaxSection to the sections list.
    6. Using all FY/Owner-filtered transactions, calculate the overall tax summary by gain type.
    7. Return a TaxView containing:
       - the provided FY
       - the requested Owner (or "ALL")
       - all TaxSections
       - the overall tax summary.
    """
    filtered = _filter_tax_transactions(tax_transactions, fy=fy, owner=owner)

    # Sorted the section groups, so that the display is consistent
    groups = sorted({
        # The comprehension below gives me a unique Asset Type + Owner combination.
        (transaction.asset_type, transaction.owner)
        for transaction in filtered
    })

    sections = []

    for asset_type, group_owner in groups:
        section_transactions = [
            # This comprehension filters for one specific Asset Type + Owner combination for further processing
            transaction
            for transaction in filtered
            if transaction.asset_type == asset_type
            and transaction.owner == group_owner
        ]
        section_transactions = _aggregate_tax_transactions(section_transactions)
        sections.append(TaxSection(asset_type=asset_type, broker=None, owner=group_owner, transactions=section_transactions,
                                   summary=_build_tax_summary(section_transactions) ) )

    return TaxView(fy=fy, owner=owner or "ALL", sections=sections, summary=_build_tax_summary(filtered) )


# ------------------------------------------------------------------
# Tax Broker view
# ------------------------------------------------------------------
def build_tax_broker_view(tax_transactions: list[TaxTransaction], fy: str, owner: str | None = None) -> TaxView:
    """
    Build the Tax Broker view. (Similar to build_tax_portfolio_view)

    Sections are grouped by:
        Broker + Owner

    The transactions within each section remain the original
    TaxTransaction records. No security-level aggregation is
    performed here.
    """
    filtered = _filter_tax_transactions(tax_transactions, fy=fy, owner=owner)

    groups = sorted({
        (transaction.broker, transaction.owner)
        for transaction in filtered
    })

    sections = []

    for broker, group_owner in groups:

        # The broker selection in the below comprehension is not needed but kept here for consistency with the portfolio view
        section_transactions = [
            transaction
            for transaction in filtered
            if transaction.broker == broker
            and transaction.owner == group_owner
        ]

        section_transactions = _aggregate_tax_transactions(section_transactions)
        
        sections.append(TaxSection(asset_type=None, broker=broker, owner=group_owner, transactions=section_transactions, 
                                   summary=_build_tax_summary(section_transactions) ) )

    return TaxView(fy=fy, owner=owner or "ALL", sections=sections, summary=_build_tax_summary(filtered) )

def build_tax_summary(tax_transactions: list[TaxTransaction]) -> list[TaxSummary]:
    """Build an overall tax summary from supplied transactions."""
    return _build_tax_summary(tax_transactions)

# ------------------------------------------------------------------
# Tax Detail
# ------------------------------------------------------------------
def build_tax_detail(tax_transactions: list[TaxTransaction], fy: str, security: str, 
                     owner: str, asset_type: str, broker: str) -> list[TaxTransaction]:
    """
    Return the tax transactions for one security.

    Selection is based on:
        FY
        Security
        Owner
        Asset Type
        Broker
    """
    return [
        transaction
        for transaction in tax_transactions
        if transaction.fy == fy
        and transaction.security == security
        and transaction.owner == owner
        and transaction.asset_type == asset_type
        and transaction.broker == broker
    ]