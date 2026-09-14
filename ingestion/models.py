"""
Normalized FinMan data models.

These models define the internal JSON-compatible structures produced
by the ingestion pipeline.

They are deliberately independent of Excel. Excel-specific details
belong to the reader and configuration-driven mapper.
"""
from datetime import date

from pydantic import BaseModel, Field

#---------------------------------------------------------
# Core Transaction models
#---------------------------------------------------------
class Transaction(BaseModel):
    """
    Normalized investment transaction.

    A Transaction represents the common structure shared by all
    supported investment datasets after Excel ingestion and mapping.
    """
    # Historical transaction fields
    security: str
    transaction_date: date
    transaction_action: str
    transaction_quantity: float
    transaction_price: float
    transaction_value: float
    position_closed: str | None = None
    owner: str
    asset_type: str
    asset_subtype: str | None = None
    broker: str | None = None
    buy_date: date | None = None
    buy_price: float | None = None

    # Current market enrichment
    # INR values are the primary portfolio values.
    current_price: float | None = None
    current_value: float | None = None

    # Historical USD transaction data and FX rate supplied by the source transaction data. 
    # These are not recalculated using today's USD/INR rate
    exchange_rate: float | None = None
    transaction_price_usd: float | None = None
    transaction_value_usd: float | None = None

    # Current USD market enrichment for US stocks.
    # current_price_usd is the raw Yahoo market price in USD;
    # current_value_usd is current_price_usd × transaction_quantity.    
    current_price_usd: float | None = None
    current_value_usd: float | None = None

class TaxTransaction(BaseModel):
    """
    Normalized tax record produced from a configured Tax worksheet.

    Represents the tax calculation associated with a sale, including
    purchase information, holding period, gain classification and tax.
    """
    fy: str
    security: str
    owner: str
    asset_type: str
    broker: str
    sale_date: date
    buy_price: float
    quantity: float
    first_purchase_date: date
    holding_period: float
    gain_type: str
    buy_value: float
    sell_value: float
    capital_gain: float
    tax_rate: float | None = None
    tax: float | None = None

    exchange_rate: float | None = None
    buy_value_usd: float | None = None
    sell_value_usd: float | None = None
    capital_gain_usd: float | None = None

#---------------------------------------------------------
# Base Holding models
#---------------------------------------------------------
class Holding(BaseModel):
    """
    Calculated holding structure used for portfolio and individual security display.

    The same structure is used for Current, Total and Closed holding views.
    """
    security: str
    owner: str
    asset_type: str
    asset_subtype: str | None = None
    broker: str | None = None

    # External research links for the security.
    # Populated when the Holding is constructed; transactions and
    # tax records deliberately do not carry these links.
    links: dict[str, dict[str, str]] = Field(default_factory=dict)

    quantity: float = 0
    cost: float = 0
    net_value: float = 0
    avg_buy_price: float | None = None

    current_price: float | None = None
    current_value: float | None = None
    gain_value: float | None = None
    gain_pct: float | None = None

    # USD values are populated for US STOCK holdings only.
    # Historical cost/average price use the USD transaction data;
    # current price/value use the current USD market price.
    usd_cost: float = 0
    usd_avg_buy_price: float | None = None
    usd_current_price: float | None = None
    usd_current_value: float | None = None
    usd_gain_value: float | None = None
    usd_gain_pct: float | None = None

    price_date: date | None = None

class ConsolidatedHolding(BaseModel):
    """
    Normalized consolidated investment record.

    These records are already summarized investments and are not
    transactions. They are used directly by the Portfolio Dashboard.
    """
    security: str
    owner: str
    asset_type: str
    asset_subtype: str | None = None
    broker: str | None = None

    invested_date: date | None = None
    maturity_date: date | None = None
    invested_value: float = 0
    current_value: float = 0

class PortfolioSummary(BaseModel):
    """ Aggregated portfolio values for a filtered set of holdings."""
    quantity: float = 0
    cost: float = 0
    net_value: float = 0
    gain_value: float = 0
    gain_pct: float | None = None

class HoldingDetail(BaseModel):
    """
    Detailed information for one security.
    Includes Current, Total and Closed holding summaries and the complete matching transaction history.
    """
    security: str
    owner: str
    asset_type: str
    asset_subtype: str | None = None
    broker: str

    transactions: list[Transaction] = Field(default_factory=list)
    current: Holding | None = None
    total: Holding | None = None
    closed: Holding | None = None

#---------------------------------------------------------
# Tax Summary Holding models
#---------------------------------------------------------
class PortfolioSection(BaseModel):
    """One asset-type section in the Portfolio view."""
    asset_type: str
     # Owner for the section even though when called by PortfolioView it might be the same 
     # owner (as PortfolioWiew) for the sections
    owner: str | None = None 
    holdings: list[Holding]
    summary: PortfolioSummary  # Summary for the section / owner

class PortfolioView(BaseModel):
    """Complete data for the Portfolio view"""
    owner: str
    sections: list[PortfolioSection]
    summary: PortfolioSummary | None = None  # Overall Summary for the Owner(s)

class BrokerSection(BaseModel):
    """One broker section in the Broker view.."""
    broker: str
     # Owner for the section even though when called by PortfolioView it might be the same 
     # owner (as PortfolioWiew) for the sections
    owner: str | None = None 
    holdings: list[Holding]
    summary: PortfolioSummary   # Summary for the section / owner

class BrokerView(BaseModel):
    """Complete data for the Broker view."""
    owner: str
    sections: list[BrokerSection]
    summary: PortfolioSummary | None = None  # Overall Summary for the Owner(s)

#---------------------------------------------------------
# Tax View Holding models
#---------------------------------------------------------
class TaxSummary(BaseModel):
    """Aggregated tax values for one gain type."""
    gain_type: str
    buy_value: float = 0
    sell_value: float = 0
    capital_gain: float = 0

class TaxSection(BaseModel):
    """One Asset Type/Owner or Broker/Owner section in a tax view."""
    asset_type: str | None = None
    broker: str | None = None
    owner: str
    transactions: list[TaxTransaction] = Field(default_factory=list)
    summary: list[TaxSummary] = Field(default_factory=list)

class TaxView(BaseModel):
    """Complete Tax Portfolio or Tax Broker view for one FY."""
    fy: str
    owner: str
    sections: list[TaxSection] = Field(default_factory=list)
    summary: list[TaxSummary] = Field(default_factory=list)

