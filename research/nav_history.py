from pydantic import BaseModel
from datetime import date, datetime

class NAVPoint(BaseModel):
    date: date
    nav: float

class NAVHistory(BaseModel):
    amfi_code: str
    fund_name: str
    history: list[NAVPoint]

class RollingReturnStats(BaseModel):
    # Display period, e.g. 1M, 3M, 6M, 1Y, 3Y, 5Y, 7Y, 10Y.
    # Optional keeps older cached research JSON backward compatible.
    period: str | None = None
    years: float | None = None
    observations: int
    min_return: float
    max_return: float
    avg_return: float
    median_return: float

class DrawdownStats(BaseModel):
    max_drawdown: float
    current_drawdown: float
    peak_nav: float
    trough_nav: float
    peak_date: date | None = None
    trough_date: date | None = None

class CalendarYearReturn(BaseModel):
    year: int
    start_nav: float
    end_nav: float
    return_pct: float

class VolatilityStats(BaseModel):
    observations: int
    volatility: float

class FundResearch(BaseModel):
    amfi_code: str
    fund_name: str
    # Short-term returns are simple period returns.
    return_1m: float | None = None
    return_3m: float | None = None
    return_6m: float | None = None

    # Long-term returns are annualised CAGR.
    cagr_1y: float | None = None
    cagr_3y: float | None = None
    cagr_5y: float | None = None
    cagr_7y: float | None = None
    cagr_10y: float | None = None

    rolling_1m: RollingReturnStats | None = None
    rolling_3m: RollingReturnStats | None = None
    rolling_6m: RollingReturnStats | None = None
    rolling_1y: RollingReturnStats | None = None
    rolling_3y: RollingReturnStats | None = None
    rolling_5y: RollingReturnStats | None = None
    rolling_7y: RollingReturnStats | None = None
    rolling_10y: RollingReturnStats | None = None
    drawdown: DrawdownStats
    volatility: VolatilityStats
    calendar_returns: list[CalendarYearReturn]

class ResearchIndex(BaseModel):
    generated_at: datetime
    funds: dict[str, FundResearch]

class RefreshAllResult(BaseModel):
    total: int
    success: int
    failed: int
    successful_amfi_codes: list[str]
    failed_amfi_codes: list[str]
