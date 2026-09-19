"""
FinMan Portfolio Builders
=========================
Basic holding unit: A Holding is a summarized holding unit position identified by: owner | asset_type | security | broker

The builders operate at different levels as follows.
(1) build_holdings()
--------------------
The basic holdings builder.
It:
    - filters transactions by owner | asset_type | broker | scope
    - groups transactions into basic Holding units
    - calculates quantity, cost, value, gain, and XIRR for each Holding
The scope determines which position is returned:
    - current
    - total
    - closed
This builder is also used when application data is refreshed to build the current holdings stored in app.state.holdings.

(2) build_portfolio_summary()
-----------------------------
Aggregates a list of Holding objects to produce a PortfolioSummary.
It sums:
    - quantity
    - cost
    - net_value
    - gain_value

Gain percentage is NOT summed. It is re-calculated from the underlying transactions because XIRR cannot be 
aggregated by summing or averaging individual holding percentages.

The summary can be filtered by:
    - owner
    - asset_type
    - broker


(3) build_holding_detail()
--------------------------
A higher-level builder for one specific Holding unit:
    security + owner | asset_type | broker
It uses build_holdings() three times to produce:
    - current
    - total
    - closed
It then combines these with the filtered transaction history to create a HoldingDetail object for the detailed holding view.

(4) build_portfolio_view()
--------------------------
A view-level builder.

It uses the already-built Holding objects and groups them by:
    asset_type + owner

Each group becomes a PortfolioSection.
The builder itself is owner-specific. It receives one actual owner (or None for ALL).

(5) build_broker_view()
-----------------------
A view-level builder.

It uses the already-built Holding objects and groups them by:
    broker + owner

Each group becomes a BrokerSection.
The builder itself is owner-specific. It receives one actual owner (or None for ALL).

How the builders are called
===========================
/portfolio?owner=XXX
--------------------
The /portfolio route:
    1. Reads the UI-selected owner.
    2. Resolves a UI owner group to one or more actual owners using config.
    3. Calls build_portfolio_view() once for each actual owner.
    4. Combines the resulting sections into the PortfolioView.
    5. Takes the holdings displayed in those sections and calls
       build_portfolio_summary() to create the overall summary.

The UI owner grouping is therefore handled by the route, not by build_portfolio_view(). 
The builder always works with one actual owner (or None for ALL).

/broker?owner=XXX
-----------------
The /broker route follows the same owner-selection pattern:
    1. Reads the UI-selected owner.
    2. Resolves it to one or more actual owners.
    3. Calls build_broker_view() once for each actual owner.
    4. Combines the resulting sections into the BrokerView.
    5. Builds the overall summary from the holdings displayed in those
       sections.
  
"""
from collections import defaultdict
from datetime import date
from typing import Literal

from ingestion.models import (Holding, Transaction, HoldingDetail, PortfolioSummary, 
                              BrokerSection, BrokerView, PortfolioSection, PortfolioView)
from market.external_links import security_links
from market.holdings import YAHOO_ASSET_TYPES_US

# A list of values when view the holding details
HoldingScope = Literal["current", "total", "closed"]
BUY_ACTIONS = frozenset({"Buy", "B", "Bonus", "DR"})
SELL_ACTIONS = frozenset({"Sell","S"})
CLOSED_STATUSES = frozenset({"CLOSED", "CLOSED OUT"})

#--------------------------------------------------------------
# Basic holdings views to be used to build specifc views
#--------------------------------------------------------------
def _filter_transactions(transactions: list[Transaction], owner: str | None, asset_type: str | None,
                         broker: str | None, scope: HoldingScope,) -> list[Transaction]:
    """Return transactions matching the requested holding scope."""
    filtered = []

    for transaction in transactions:
        if owner is not None and transaction.owner != owner:
            continue

        if asset_type is not None and transaction.asset_type != asset_type:
            continue

        if broker is not None and transaction.broker != broker:
            continue

        closed = transaction.position_closed in CLOSED_STATUSES

        # In case of current values scope is "current" and position should not be CLOSED
        if scope == "current" and closed:
            continue

        # In case of closed values scope is "closed" and position alos  should be CLOSED
        if scope == "closed" and not closed:
            continue

        # In case of total / all values, scope is "total" and grab all values
        # Also grab value where scope="current" and position not CLOSED OR where scope="closed" and position is CLOSED
        filtered.append(transaction)

    return filtered

def build_holdings(transactions: list[Transaction], scope: HoldingScope = "current", owner: str | None = None,
                   asset_type: str | None = None, broker: str | None = None,) -> list[Holding]:
    """Build holdings for the requested current, total or closed scope."""
    filtered = _filter_transactions(transactions, owner, asset_type, broker, scope)

    # Whenever I ask for a key that doesn't exist, automatically create an empty list for that key within group.
    # groups[key].append(transaction) works even when key has never been seen before.
    groups = defaultdict(list)  

    for transaction in filtered:
        # Holding identity = security + owner + asset_type + broker.
        # asset_subtype is descriptive and must not split a Holding.        
        key = (transaction.security, transaction.owner, transaction.asset_type,transaction.broker)
        groups[key].append(transaction)

    holdings = []

    for group in groups.values():
        first = group[0]   # This is for getting the security, owner, asset_typ, asset_subtype, broker for return values

        # 1. Get quantity
        # Quantity is same for current and total transactions (though we still make that distcintion here)
        # Quantity has a different meaning for each scope:
        # current = open position, total = open position, closed = units sold.
        if scope == "total":
            open_transactions = [
                t for t in group
                if t.position_closed not in CLOSED_STATUSES
            ]
            quantity = sum(t.transaction_quantity for t in open_transactions)
        elif scope == "closed":
            quantity = sum(
                t.transaction_quantity
                for t in group
                if t.transaction_action in SELL_ACTIONS
            )
        else:
            quantity = sum(t.transaction_quantity for t in group)

        # 2. Get cost
        # Cost definition depends on the holding scope:
        # current = open transaction values
        # total   = buy transaction values
        # closed  = closed buy transaction values
        cost_transactions = [
            t for t in group
            if t.transaction_action in BUY_ACTIONS
        ]
        cost = -sum(
            t.transaction_value
            for t in cost_transactions
        )

        # 3. Get transaction value
        # Sum of all historical transaction values in this group.
        net_value = sum(
            t.transaction_value
            for t in group
        )

        # 4. Get current market value
        # Sum of the current values populated during price refresh.
        current_values = [
            t.current_value
            for t in group
            if t.current_value is not None
        ]
        current_value = (
            sum(current_values)
            if current_values
            else None
        )

        # 5. Get current price
        # next -> Find the first thing that satisfies this condition; if there isn't one, give me None.
        current_price = next(
            (
                t.current_price
                for t in group
                if t.current_price is not None
            ),
            None,
        )

        # 6. Get Average buy Price
        # Closed quantity is negative because it represents sold units.
        # Use its absolute value when calculating average buy price.
        avg_buy_price = (
            cost / abs(quantity)
            if quantity
            else None
        )

        # 7. USD values for US stocks
        # Historical USD cost and average buy price come directly from the USD transaction values 
        # supplied by the source data. Current USD value uses the current market price in USD.
        # No historical INR amounts are converted using today's FX rate.        
        usd_cost = 0
        usd_avg_buy_price = None
        usd_current_price = None
        usd_current_value = None
        usd_gain_value = None
        usd_gain_pct = None

        if first.asset_type in YAHOO_ASSET_TYPES_US:
            usd_cost_transactions = [
                t for t in group
                if t.transaction_action in BUY_ACTIONS
                and t.transaction_value_usd is not None
            ]

            usd_cost = -sum(
                t.transaction_value_usd
                for t in usd_cost_transactions
            )

            usd_avg_buy_price = (
                usd_cost / abs(quantity)
                if quantity
                else None
            )

            usd_current_price = next(
                (
                    t.current_price_usd
                    for t in group
                    if t.current_price_usd is not None
                ),
                None,
            )

            usd_current_values = [
                t.current_value_usd
                for t in group
                if t.current_value_usd is not None
            ]

            usd_current_value = (
                sum(usd_current_values)
                if usd_current_values
                else None
            )

            if scope == "current":
                usd_gain_value = (
                    usd_current_value - usd_cost
                    if usd_current_value is not None
                    else None
                )

                if usd_current_value is not None:
                    usd_gain_pct = _calculate_transaction_xirr_usd(
                        group,
                        usd_current_value,
                    )

        # 8. Get the net gain and net gain percent in INR   
        # USD gain is calculated separately above for US stocks.
        # Current and Total include today's market value as the terminal cash flow.
        # Closed uses realized transaction cash flows only.
        if scope == "current":
            holding_value = current_value
            gain_value = (
                current_value - cost
                if current_value is not None
                else None
            )
            gain_pct = (
                _calculate_transaction_xirr(group, current_value)
                if current_value is not None
                else None
            )
            if first.asset_type in YAHOO_ASSET_TYPES_US and usd_current_value is not None:
                usd_gain_value = usd_current_value - usd_cost
                usd_gain_pct = _calculate_transaction_xirr_usd(
                    group,
                    usd_current_value,
                )            
        elif scope == "total":
            holding_value = net_value
            gain_value = net_value + (current_value or 0)
            gain_pct = _calculate_transaction_xirr(group, current_value)
        else:
            holding_value = net_value
            gain_value = net_value
            gain_pct = _calculate_transaction_xirr(group)

        holdings.append(
            Holding(
                security=first.security,
                owner=first.owner,
                asset_type=first.asset_type,
                asset_subtype=first.asset_subtype,
                broker=first.broker,

                # External research links belong to the Holding, not to individual transactions. 
                # This means one security's links are carried once per Holding object.
                links=security_links(
                    security=first.security,
                    asset_type=first.asset_type,
                ),

                quantity=quantity,
                cost=cost,
                net_value=holding_value if holding_value is not None else 0,
                avg_buy_price=avg_buy_price,

                current_price=current_price,
                current_value=current_value,
                gain_value=gain_value,
                gain_pct=gain_pct,

                usd_cost=usd_cost,
                usd_avg_buy_price=usd_avg_buy_price,
                usd_current_price=usd_current_price,
                usd_current_value=usd_current_value,
                usd_gain_value=usd_gain_value,
                usd_gain_pct=usd_gain_pct,

                price_date=None,
            )
        )

    return holdings

def build_current_holdings(transactions: list[Transaction], owner: str | None = None, 
                           asset_type: str | None = None, broker: str | None = None,) -> list[Holding]:
    """Build current holdings from open transactions."""

    return build_holdings(
        transactions,
        scope="current",
        owner=owner,
        asset_type=asset_type,
        broker=broker,
    )

def build_portfolio_summary(holdings: list[Holding], transactions: list[Transaction], owner: str | None = None, 
                            asset_type: str | None = None, broker: str | None = None,) -> PortfolioSummary:
    """Build an aggregate portfolio summary from holdings."""

    filtered = [
        holding
        for holding in holdings
        if (owner is None or holding.owner == owner)
        and (asset_type is None or holding.asset_type == asset_type)
        and (broker is None or holding.broker == broker)
    ]

    quantity = sum(h.quantity for h in filtered)
    cost = sum(h.cost for h in filtered)
    net_value = sum(h.net_value for h in filtered)

    return PortfolioSummary(
        quantity=quantity,
        cost=cost,
        net_value=net_value,
        gain_value=net_value - cost,
        gain_pct=calculate_portfolio_gain(
            transactions,
            owner=owner,
            asset_type=asset_type,
            broker=broker,
        )
    )


#--------------------------------------------------------------
# XIRR Calculation
#--------------------------------------------------------------
def calculate_xirr(cash_flows: list[float], dates: list[date]) -> float | None:
    """
    Calculate annualized return for dated cash flows.
    Returns None when the cash flows cannot produce a meaningful XIRR.
    """
    if len(cash_flows) != len(dates) or len(cash_flows) < 2:
        return None

    if not any(value < 0 for value in cash_flows):
        return None

    if not any(value > 0 for value in cash_flows):
        return None

    start_date = dates[0]
    years = [
        (current_date - start_date).days / 365
        for current_date in dates
    ]

    def npv(rate: float) -> float:
        return sum(
            value / (1 + rate) ** year
            for value, year in zip(cash_flows, years)
        )

    low = -0.999999
    high = 1.0

    low_value = npv(low)
    high_value = npv(high)

    while low_value * high_value > 0 and high < 1_000_000:
        high *= 2
        high_value = npv(high)

    if low_value * high_value > 0:
        return None

    for _ in range(100):
        mid = (low + high) / 2
        mid_value = npv(mid)

        if abs(mid_value) < 0.01:
            return mid

        if low_value * mid_value <= 0:
            high = mid
            high_value = mid_value
        else:
            low = mid
            low_value = mid_value

    return (low + high) / 2

def _calculate_transaction_xirr(transactions: list[Transaction], terminal_value: float | None = None) -> float | None:
    """
    Calculate XIRR from transaction values and dates.
    Adds the terminal value at today's date when supplied.
    """
    cash_flows = [
        transaction.transaction_value
        for transaction in transactions
    ]

    dates = [
        transaction.transaction_date
        for transaction in transactions
    ]

    if terminal_value is not None:
        cash_flows.append(terminal_value)
        dates.append(date.today())

    return calculate_xirr(cash_flows, dates)


def _calculate_transaction_xirr_usd(transactions: list[Transaction], terminal_value: float | None = None,) -> float | None:
    """
    Calculate XIRR using USD transaction values and dates.

    For current holdings, the current USD market value is added as the terminal cash flow at today's date.    
    """
    cash_flows = [
        transaction.transaction_value_usd
        for transaction in transactions
        if transaction.transaction_value_usd is not None
    ]

    dates = [
        transaction.transaction_date
        for transaction in transactions
        if transaction.transaction_value_usd is not None
    ]

    if terminal_value is not None:
        cash_flows.append(terminal_value)
        dates.append(date.today())

    return calculate_xirr(cash_flows, dates)

def calculate_portfolio_gain(transactions: list[Transaction], owner: str | None = None, 
                             asset_type: str | None = None, broker: str | None = None) -> float | None:
    """
    Calculate Current Gain % for a filtered portfolio.
    Uses open transaction cash flows and current value as the terminal value.
    """    
    filtered = [
        transaction
        for transaction in transactions
        if not transaction.position_closed
        and (owner is None or transaction.owner == owner)
        and (asset_type is None or transaction.asset_type == asset_type)
        and (broker is None or transaction.broker == broker)
    ]

    current_values = [
        transaction.current_value
        for transaction in filtered
        if transaction.current_value is not None
    ]

    if not filtered or not current_values:
        return None

    current_value = sum(current_values)

    return _calculate_transaction_xirr(filtered, current_value)

#--------------------------------------------------------------
# Build Views for the front-end.
#--------------------------------------------------------------
def build_holding_detail(
    transactions: list[Transaction], security: str, owner: str,asset_type: str,broker: str) -> HoldingDetail | None:
    """
    Build detailed current, total and closed holdings for a specifc security, owner, asset_type and broker.
    The returned detail also contains the complete transaction history for the selected security and filters.
    """
    filtered = [
        transaction
        for transaction in transactions
        if transaction.security == security
        and transaction.owner == owner
        and transaction.asset_type == asset_type
        and transaction.broker == broker
    ]

    if not filtered:
        return None

    current = build_holdings(transactions, scope="current",owner=owner, 
                             asset_type=asset_type,broker=broker)

    total = build_holdings(transactions, scope="total", owner=owner, 
                           asset_type=asset_type,broker=broker)

    closed = build_holdings(transactions, scope="closed", owner=owner,
                            asset_type=asset_type, broker=broker)

    current = next(
        (holding for holding in current if holding.security == security),
        None,
    )

    total = next(
        (holding for holding in total if holding.security == security),
        None,
    )

    closed = next(
        (holding for holding in closed if holding.security == security),
        None,
    )

    first = filtered[0]

    return HoldingDetail(
        security=first.security,
        owner=first.owner,
        asset_type=first.asset_type,
        asset_subtype=first.asset_subtype,
        broker=first.broker,
        transactions=filtered,
        current=current,
        total=total,
        closed=closed,
    )

def build_portfolio_view(transactions: list[Transaction], holdings: list[Holding], owner: str | None = None) -> PortfolioView:
    """Build the Portfolio view grouped by asset type and owner."""

    # Filter the holdings based on owner or none for ALL
    filtered_holdings = [
        holding
        for holding in holdings
        if owner is None or holding.owner == owner
    ]

    sections = []   

    # Sort the result by asset_type followed by Owner
    groups = sorted({
        (holding.asset_type, holding.owner)
        for holding in filtered_holdings
    })

    # Based on the asset_type and Owner create section_holdings
    for asset_type, group_owner in groups:
        section_holdings = [
            holding for holding in filtered_holdings
            if holding.asset_type == asset_type
            and holding.owner == group_owner
        ]

        # For each of the section holdings get the summary or totals
        summary = build_portfolio_summary(section_holdings, transactions, owner=group_owner, asset_type=asset_type)

        # Attach the sections one after another
        sections.append(PortfolioSection(asset_type=asset_type, owner=group_owner, summary=summary, holdings=section_holdings))

    return PortfolioView(owner=owner or "ALL", sections=sections)

def build_broker_view(transactions: list[Transaction], holdings: list[Holding], owner: str | None = None) -> BrokerView:
    """Build the Broker view grouped by broker and owner."""

    # Filter the holdings based on owner or none for ALL
    filtered_holdings = [
        holding
        for holding in holdings
        if owner is None or holding.owner == owner
    ]

    sections = []

    # Sort the result by broker followed by Owner
    groups = sorted({
        (holding.broker, holding.owner)
        for holding in filtered_holdings
    })

    # Create holdings for this asset type / owner section
    for broker, group_owner in groups:
        section_holdings = [
            holding
            for holding in filtered_holdings
            if holding.broker == broker
            and holding.owner == group_owner
        ]

        # For each of the section holdings get the summary or totals
        summary = build_portfolio_summary(section_holdings, transactions, owner=group_owner, broker=broker)

        # Attach the sections one after another
        sections.append(BrokerSection(broker=broker, owner=group_owner, summary=summary, holdings=section_holdings))

    return BrokerView(owner=owner or "ALL", sections=sections)
