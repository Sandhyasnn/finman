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
    years: int
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
    cagr_1y: float | None = None
    cagr_3y: float | None = None
    cagr_5y: float | None = None
    rolling_1y: RollingReturnStats | None = None
    rolling_3y: RollingReturnStats | None = None
    rolling_5y: RollingReturnStats | None = None
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
