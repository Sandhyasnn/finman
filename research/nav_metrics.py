from datetime import date, timedelta
from calendar import monthrange
from bisect import bisect_right
from math import sqrt
from statistics import mean, median, stdev

from research.nav_history import (
    NAVHistory, 
    RollingReturnStats, 
    DrawdownStats, 
    CalendarYearReturn,
    VolatilityStats,
    RollingOutperformanceStats
)

#---------------------------------------------------------------
# Helper functions
#---------------------------------------------------------------
def calculate_cagr(start_value: float, end_value: float, years: float) -> float: 
    """
    Returns as a fraction, e.g. 0.1714 for 17.14%
    CAGR = (Ending value/Beginning value)^(1/n) - 1
    """
    if start_value <= 0 or end_value <= 0:
        return 0.0

    cagr = (end_value / start_value) ** (1 / years) - 1
    return cagr

def get_nav_before_date(history, target_date):
    """
    Finds NAV closest on or before the target date. This is used in trailing CAGR
    """
    # Get a list of just dates from oldest to newest - rememvber history is chronologically sorted
    dates = [point.date for point in history]

    # bisect_right does a binary search. It tries to insert 'target_date' into a sorted list of
    # 'dates' and returns the index of that insertion. The value before that (hence -1) gives the
    # value we are seeking
    index = bisect_right(dates, target_date) - 1

    if index < 0:
        return None

    return history[index]

def add_months(value: date, months: int) -> date:
    """
    Add calendar months while keeping the day valid for the target month.

    Example:
        31-Jan + 1 month -> 28-Feb
        31-Jan + 3 months -> 30-Apr
    """
    # Converts the current month (1–12) into a 0-indexed format (0–11) by subtracting 1, 
    # then adds the requested months offset. This makes it easy to handle wrapping across 
    # multiple years (both forward and backward if months is negative).    
    month_index = value.month - 1 + months
    # Uses integer division (// 12) to determine how many full years to add to (or subtract from)
    # the original year.
    year = value.year + month_index // 12
    # Uses modulo arithmetic (% 12) to find the remaining month index, then adds 1 back to 
    # return to the standard 1–12 month numbering system.
    month = month_index % 12 + 1

    # monthrange(year, month): A function from Python’s standard calendar module that returns 
    # a tuple (weekday_of_first_day, number_of_days_in_month). The [1] accesses the maximum 
    # number of days in that target month.
    # min(...): This is the secret sauce. If you add 1 month to January 31st, the target month
    # is February. Since February doesn't have 31 days, monthrange returns 28 (or 29 in a leap year).
    # The min() function ensures the day drops down to 28 instead of raising a ValueError for an
    # invalid date.
    day = min(value.day, monthrange(year, month)[1])

    return date(year, month, day)

def _series_value(point):
    """Return the numeric value from either NAVPoint or BenchmarkPoint."""
    if hasattr(point, "nav"):
        return point.nav
    if hasattr(point, "tri"):
        return point.tri
    raise ValueError("Unsupported research data point")

def _history_points(history):
    """Return sorted points from either NAVHistory or BenchmarkHistory."""
    # Can accept either a model or a list, so it can receive NAVHistory or a plain history list
    points = history.history if hasattr(history, "history") else history

    # Before wasting resources sorting the list, it checks if the data is already sorted in 
    # ascending order.It uses a generator expression with all() to verify that every point's 
    # date is less than or equal to the date of the point immediately following it. If the list 
    # is already sorted, it returns the original list immediately, avoiding the computational 
    # overhead of a full sort. This runs in $O(N)$ time.
    if all(
        points[i].date <= points[i + 1].date
        for i in range(len(points) - 1)
    ):
        return points

    # Sorting fallback
    return sorted(points, key=lambda p: p.date)

#---------------------------------------------------------------
# Main Calculation / Perofrmance indicators functions
#---------------------------------------------------------------
def calc_trailing_cagr(history, years):
    # Calculates the CAGR from the latest available time (as provided in 'hisory')
    # to the last time backward by 'years'. If years=3, then from current to 3 yrs ago CAGR
    if not history or len(history) < 2:
        return None

    points = _history_points(history)

    latest = points[-1]
    target_date = (latest.date - timedelta(days=int(years * 365.25)) )

    historical = get_nav_before_date(points, target_date)

    if not historical:
        return None

    return calculate_cagr(_series_value(historical), _series_value(latest), years)

def calc_trailing_return_months(history, months):
    """
    Calculate the actual, non-annualised return over a number of calendar months.

    Example:
        3M return = NAV today / NAV 3 calendar months ago - 1
    """
    if not history or len(history) < 2:
        return None

    points = _history_points(history)

    latest = points[-1]   # Grab the last item in the list i.e latest data point
    target_date = add_months(latest.date, -months)  # move back 'months' from the 'latest' date

    historical = get_nav_before_date(points, target_date)

    if not historical:
        return None

    start_value = _series_value(historical)
    end_value = _series_value(latest)

    if start_value <= 0 or end_value <= 0:
        return None

    return (end_value / start_value) - 1

def calc_rolling_returns(nav_history, years: int,) -> RollingReturnStats | None:
    '''
    Calculate the CAGR for every possible starting point over the specified period.
    if 'years'=3, then it calculates the CAGR for all the starting point for 3 years, 2010-13, 2011-14,etc
    Example:
        years=3 calculates 3-year rolling returns for all possible starting points in the NAV history.

    The end-date lookup uses a moving pointer rather than scanning the complete history for every start point.
    '''    
    history = _history_points(nav_history)

    rolling_returns: list[float] = []

    target_days = int(years * 365.25)

    # Target dates move forward as start_point moves forward.
    # Therefore the end-point index only needs to move forward.
    end_index = 0

    for start_point in history:
        target_date = (start_point.date + timedelta(days=target_days) )   # Target date = startdate + years

        # Find the first NAV on or after the target date. Since target_date only moves forward, 
        # end_index never needs to move backwards.
        while (end_index < len(history) and history[end_index].date < target_date):
            end_index += 1

        if end_index >= len(history):
            continue

        end_point = history[end_index]

        start_value = _series_value(start_point)
        end_value = _series_value(end_point)

        if start_value <= 0 or end_value <= 0:
            continue

        cagr = calculate_cagr(start_value, end_value, years)

        # Accumulate all the rolling returns
        rolling_returns.append(cagr)

    if not rolling_returns:
        # raise ValueError(f"Insufficient history for {years}Y rolling returns")
        return None

    return RollingReturnStats(
        years=years,
        observations=len(rolling_returns),

        min_return=min(rolling_returns),
        max_return=max(rolling_returns),
        avg_return=mean(rolling_returns),
        median_return=median(rolling_returns)
    )

def calc_rolling_returns_months(nav_history: NAVHistory, months: int) -> RollingReturnStats | None:
    """
    Calculate actual (non-annualised) returns for every possible
    starting point over the specified number of calendar months.

    Example:
        3M rolling return:
        start NAV → NAV approximately 3 calendar months later.

    The return is:
        (end NAV / start NAV) - 1
    """
    history = _history_points(nav_history)

    rolling_returns: list[float] = []
    end_index = 0

    for start_point in history:
        target_date = add_months(start_point.date, months)

        # Loops forward on 'history'. So, the end_index for loops after the first are always beyond 
        # + 'months' of the start_date doing only a forward lookup
        while (end_index < len(history) and history[end_index].date < target_date):
            end_index += 1

        if end_index >= len(history):
            break

        end_point = history[end_index]

        start_value = _series_value(start_point)
        end_value = _series_value(end_point)
        if start_value <= 0 or end_value <= 0:
            continue
        rolling_returns.append((end_value / start_value) - 1)

    if not rolling_returns:
        return None

    return RollingReturnStats(
        years=0,
        observations=len(rolling_returns),
        min_return=min(rolling_returns),
        max_return=max(rolling_returns),
        avg_return=mean(rolling_returns),
        median_return=median(rolling_returns),
    )

def calc_rolling_outperformance(fund_history, benchmark_history, *,
                                 years: int | None = None, months: int | None = None):
    fund_points = _history_points(fund_history)
    benchmark_points = _history_points(benchmark_history)

    if not fund_points or not benchmark_points:
        return None

    outperformance = []

    fund_end_idx = 0
    benchmark_start_idx = -1
    benchmark_end_idx = -1

    for start_point in fund_points:
        if years is not None:
            target_date = add_months(start_point.date, years * 12)
        else:
            target_date = add_months(start_point.date, months)

        while (
            fund_end_idx < len(fund_points)
            and fund_points[fund_end_idx].date < target_date
        ):
            fund_end_idx += 1

        if fund_end_idx >= len(fund_points):
            break

        end_point = fund_points[fund_end_idx]

        while (
            benchmark_start_idx + 1 < len(benchmark_points)
            and benchmark_points[benchmark_start_idx + 1].date
            <= start_point.date
        ):
            benchmark_start_idx += 1

        while (
            benchmark_end_idx + 1 < len(benchmark_points)
            and benchmark_points[benchmark_end_idx + 1].date
            <= end_point.date
        ):
            benchmark_end_idx += 1

        if benchmark_start_idx < 0 or benchmark_end_idx < 0:
            continue

        benchmark_start = benchmark_points[benchmark_start_idx]
        benchmark_end = benchmark_points[benchmark_end_idx]

        if benchmark_start.date > start_point.date:
            continue

        if benchmark_end.date < end_point.date:
            continue

        fund_start_value = _series_value(start_point)
        fund_end_value = _series_value(end_point)

        benchmark_start_value = _series_value(benchmark_start)
        benchmark_end_value = _series_value(benchmark_end)

        if (
            fund_start_value <= 0
            or fund_end_value <= 0
            or benchmark_start_value <= 0
            or benchmark_end_value <= 0
        ):
            continue

        if years is not None:
            fund_return = calculate_cagr(fund_start_value, fund_end_value,years)
            benchmark_return = calculate_cagr(benchmark_start_value, benchmark_end_value, years)
        else:
            # These will be monthly periods so absolute returns
            fund_return = (fund_end_value / fund_start_value) - 1
            benchmark_return = (benchmark_end_value / benchmark_start_value) - 1

        outperformance.append(fund_return - benchmark_return)

    if not outperformance:
        return None

    observations = len(outperformance)
    periods_outperformed = sum(1 for value in outperformance if value > 0)

    return RollingOutperformanceStats(
        years=years or 0,
        observations=observations,
        min_outperformance=min(outperformance),
        max_outperformance=max(outperformance),
        avg_outperformance=sum(outperformance) / observations,
        median_outperformance=median(outperformance),
        periods_outperformed=periods_outperformed,
        pct_periods_outperformed=(
            periods_outperformed / observations
        ),
    )

def calc_drawdown(nav_history: NAVHistory) -> DrawdownStats:
    history = _history_points(nav_history)

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
    history = _history_points(nav_history)

    results: list[CalendarYearReturn] = []
    current_year = date.today().year
    yearly_points = {}

    for point in history:
        year = point.date.year
        if year == current_year:
            continue   # Ignore data points in current year

        # If you hit the year for the first it enters the if part and sets both the values in 
        # yearly_points to the same. NExt time you encounter a data point with the same year you only 
        # update the seoncd part [year][1] successively until the last - rememver history is already sorted
        # so when you exit the loop for the year you have the first and last point in yearly_points
        if year not in yearly_points:
            yearly_points[year] = [point, point]
        else:
            yearly_points[year][1] = point

    # Calculate the CAGR for each year and create CalendarYearReturn for each year
    for year, (first_point, last_point) in yearly_points.items():
        start_nav = first_point.nav
        end_nav = last_point.nav

        if start_nav <= 0 or end_nav <= 0:
            continue

        results.append(CalendarYearReturn(year=year, start_nav=start_nav, end_nav=end_nav, 
                               return_pct=(end_nav / start_nav) - 1) )

    return results

def calc_volatility(nav_history: NAVHistory,) -> VolatilityStats:
    # Calculates daily volatility by calculating daily returns and computing the standard devation on it X sqrt(252)
    history = _history_points(nav_history)

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

