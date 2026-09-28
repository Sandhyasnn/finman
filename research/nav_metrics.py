from datetime import date, timedelta
from calendar import monthrange
from math import sqrt
from statistics import mean, median, stdev

from research.nav_history import (
    NAVHistory, 
    RollingReturnStats, 
    DrawdownStats, 
    CalendarYearReturn,
    VolatilityStats
)

def calculate_cagr(start_nav: float, end_nav: float, years: float) -> float:
    """
    Returns as a fraction, e.g. 0.1714 for 17.14%
    CAGR = (Ending value/Beginning value)^(1/n) - 1
    """
    if start_nav <= 0:
        return 0.0

    cagr = (end_nav / start_nav) ** (1 / years) - 1
    return cagr

def get_nav_before_date(history, target_date):
    """
    Finds NAV closest on or before the target date. This is used in trailing CAGR
    """
    for point in history:
        if point.date <= target_date:
            return point

    return None

def add_months(value: date, months: int) -> date:
    """Add calendar months while keeping the day within the target month."""
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, monthrange(year, month)[1])
    return date(year, month, day)


def calc_trailing_return_months(history, months: int):
    """Calculate the actual (non-annualised) return over a calendar-month period."""
    latest = history[0]
    target_date = add_months(latest.date, -months)
    historical = get_nav_before_date(history, target_date)

    if not historical or historical.nav <= 0 or latest.nav <= 0:
        return None

    return (latest.nav / historical.nav) - 1


def calc_trailing_cagr(history, years):
    # Calculates the CAGR from the latest available time (as provided in 'history')
    # to the last time backward by 'years'.
    latest = history[0]

    target_date = latest.date - timedelta(days=int(years * 365.25))
    historical = get_nav_before_date(history, target_date)

    if not historical:
        return None

    return calculate_cagr(historical.nav, latest.nav, years)


def calc_rolling_returns(nav_history: NAVHistory, years: int,) -> RollingReturnStats:
    '''
    Calculate annualised CAGR for every possible starting point over the specified
    number of years.
    '''
    history = sorted(nav_history.history, key=lambda p: p.date)
    rolling_returns: list[float] = []
    target_days = int(years * 365.25)
    end_index = 0

    for start_point in history:
        target_date = start_point.date + timedelta(days=target_days)

        while (
            end_index < len(history)
            and history[end_index].date < target_date
        ):
            end_index += 1

        if end_index >= len(history) or start_point.nav <= 0:
            continue

        end_point = history[end_index]
        cagr = (end_point.nav / start_point.nav) ** (1 / years) - 1
        rolling_returns.append(cagr)

    if not rolling_returns:
        raise ValueError(f"Insufficient history for {years}Y rolling returns")

    return RollingReturnStats(
        period=f"{years}Y",
        years=years,
        observations=len(rolling_returns),
        min_return=min(rolling_returns),
        max_return=max(rolling_returns),
        avg_return=mean(rolling_returns),
        median_return=median(rolling_returns)
    )


def calc_rolling_returns_months(nav_history: NAVHistory, months: int) -> RollingReturnStats:
    '''
    Calculate simple (non-annualised) returns for every possible starting point
    over the specified number of calendar months.
    '''
    history = sorted(nav_history.history, key=lambda p: p.date)
    rolling_returns: list[float] = []
    end_index = 0

    for start_point in history:
        target_date = add_months(start_point.date, months)

        while (
            end_index < len(history)
            and history[end_index].date < target_date
        ):
            end_index += 1

        if end_index >= len(history) or start_point.nav <= 0:
            continue

        end_point = history[end_index]

        if end_point.nav <= 0:
            continue

        period_return = (end_point.nav / start_point.nav) - 1
        rolling_returns.append(period_return)

    if not rolling_returns:
        raise ValueError(f"Insufficient history for {months}M rolling returns")

    return RollingReturnStats(
        period=f"{months}M",
        years=None,
        observations=len(rolling_returns),
        min_return=min(rolling_returns),
        max_return=max(rolling_returns),
        avg_return=mean(rolling_returns),
        median_return=median(rolling_returns)
    )

def calc_drawdown(nav_history: NAVHistory) -> DrawdownStats:
    history = sorted(nav_history.history, key=lambda p: p.date)

    if not history:
        raise ValueError("No valid NAV history")

    if len(history) < 2:
        raise ValueError("Insufficient valid NAV history")

    peak_nav = history[0].nav
    peak_date = history[0].date

    max_drawdown = 0.0

    max_peak_date = peak_date
    max_peak_nav = peak_nav

    max_trough_date = peak_date
    max_trough_nav = peak_nav

    for point in history:

        if point.nav > peak_nav:
            peak_nav = point.nav
            peak_date = point.date

        drawdown = (point.nav - peak_nav) / peak_nav

        if drawdown < max_drawdown:
            max_drawdown = drawdown
            max_peak_date = peak_date
            max_peak_nav = peak_nav
            max_trough_date = point.date
            max_trough_nav = point.nav

    latest_nav = history[-1].nav

    current_drawdown = (latest_nav - peak_nav) / peak_nav

    return DrawdownStats(
        max_drawdown=max_drawdown,
        current_drawdown=current_drawdown,  
        peak_nav=max_peak_nav,
        trough_nav=max_trough_nav,
        peak_date=max_peak_date,
        trough_date=max_trough_date,
    )

def calc_calendar_year_returns(nav_history: NAVHistory) -> list[CalendarYearReturn]:
    # Calcutate the returns based on calendar year. For that group NAVHistory by calendar year
    history = sorted(nav_history.history, key=lambda p: p.date)

    results: list[CalendarYearReturn] = []

    years = sorted({point.date.year for point in history} )

    for year in years:
        # Exclude currernt since the full year will not be over
        if year == date.today().year:
            continue

        # Get all the NAVHIstory items for the current 'year'. Remember this is already sorted
        points = [
            p
            for p in history
            if p.date.year == year
        ]

        # Need at least two NAV observations to calculate the year's return.
        if len(points) < 2:
            continue

        # Pick the first and last items in points which is accumulation for 'year'. Remember this is already sorted
        start_nav = points[0].nav
        end_nav = points[-1].nav
        if start_nav <= 0 or end_nav <= 0:   # Skip if there are no points/items at all
            continue
        return_pct = (end_nav / start_nav) - 1   # Calucate a simple % since this is only 1 year

        results.append(
            CalendarYearReturn(
                year=year,
                start_nav=start_nav,
                end_nav=end_nav,
                return_pct=return_pct,
            )
        )

    return results

def calc_volatility(nav_history: NAVHistory,) -> VolatilityStats:
    # Calculates daily volatility by calculating daily returns and computing the standard devation on it X sqrt(252)
    history = sorted(nav_history.history, key=lambda p: p.date)

    returns = []

    for i in range(1, len(history)):
        previous = history[i - 1]
        current = history[i]

        if previous.nav <= 0 or current.nav <= 0:
            continue

        return_pct = ( (current.nav / previous.nav) - 1)  # This is simple period (daily in this case) returns 

        returns.append(return_pct)

    if len(returns) < 2:
        raise ValueError("Insufficient history")

    # sqrt{(252) factor is only correct if the points are (approximately) one trading day apart.
    annualized_volatility = stdev(returns) * sqrt(252)

    return VolatilityStats(observations=len(returns), volatility=annualized_volatility)

