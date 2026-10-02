from pydantic import BaseModel
from datetime import date, datetime

class NAVPoint(BaseModel):
    date: date
    nav: float

class NAVHistory(BaseModel):
    amfi_code: str
    fund_name: str
    history: list[NAVPoint]

class BenchmarkPoint(BaseModel):
    date: date
    tri: float
class BenchmarkHistory(BaseModel):
    benchmark: str
    requested_start: date | None = None
    requested_end: date | None = None
    history: list[BenchmarkPoint]
class RollingOutperformanceStats(BaseModel):
    years: int
    observations: int
    min_outperformance: float
    max_outperformance: float
    avg_outperformance: float
    median_outperformance: float
    periods_outperformed: int
    pct_periods_outperformed: float
class RollingReturnStats(BaseModel):
    years: int
    observations: int
    min_return: float
    max_return: float
    avg_return: float
    median_return: float
class BenchmarkComparison(BaseModel):
    benchmark: str
    benchmark_name: str | None = None

    benchmark_return_1m: float | None = None
    benchmark_return_3m: float | None = None
    benchmark_return_6m: float | None = None

    benchmark_cagr_1y: float | None = None
    benchmark_cagr_3y: float | None = None
    benchmark_cagr_5y: float | None = None
    benchmark_cagr_7y: float | None = None
    benchmark_cagr_10y: float | None = None

    outperformance_1m: float | None = None
    outperformance_3m: float | None = None
    outperformance_6m: float | None = None

    outperformance_1y: float | None = None
    outperformance_3y: float | None = None
    outperformance_5y: float | None = None
    outperformance_7y: float | None = None
    outperformance_10y: float | None = None

    benchmark_rolling_1m: RollingReturnStats | None = None
    benchmark_rolling_3m: RollingReturnStats | None = None
    benchmark_rolling_6m: RollingReturnStats | None = None

    benchmark_rolling_1y: RollingReturnStats | None = None
    benchmark_rolling_3y: RollingReturnStats | None = None
    benchmark_rolling_5y: RollingReturnStats | None = None
    benchmark_rolling_7y: RollingReturnStats | None = None
    benchmark_rolling_10y: RollingReturnStats | None = None

    rolling_outperformance_1m: RollingOutperformanceStats | None = None
    rolling_outperformance_3m: RollingOutperformanceStats | None = None
    rolling_outperformance_6m: RollingOutperformanceStats | None = None

    rolling_outperformance_1y: RollingOutperformanceStats | None = None
    rolling_outperformance_3y: RollingOutperformanceStats | None = None
    rolling_outperformance_5y: RollingOutperformanceStats | None = None
    rolling_outperformance_7y: RollingOutperformanceStats | None = None
    rolling_outperformance_10y: RollingOutperformanceStats | None = None 
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

    return_1m: float | None = None
    return_3m: float | None = None
    return_6m: float | None = None    
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

    benchmark: BenchmarkComparison | None = None
    
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
    
    benchmark_available: int
    benchmark_unavailable: int
    benchmark_unavailable_details: list[str]
    
    insufficient_history: int   # Insufficient history for rolling return calculations
    insufficient_history_details: dict[str, list[str]]
