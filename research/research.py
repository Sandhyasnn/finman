from datetime import date, datetime
from pathlib import Path
import threading
from functools import lru_cache

import logging
logger = logging.getLogger("finman")

from research.nav_history import (
    NAVHistory, 
    FundResearch,
    ResearchIndex,
    RefreshAllResult,
    BenchmarkPoint,
    BenchmarkHistory,
    BenchmarkComparison,
)

from research.download_nav_history import (
    download_nav_history,
    save_nav_history
)

from research.nav_metrics import (
    calc_trailing_cagr,
    calc_trailing_return_months,    
    calc_rolling_returns,
    calc_rolling_returns_months,
    calc_drawdown,
    calc_calendar_year_returns,
    calc_volatility,
    calc_rolling_outperformance
)

from research.nifty_tri import fetch_nifty_tri
from ingestion.json_io import read_json, write_json

from market.external_links import load_mf_master, load_benchmark_master

from concurrent.futures import ThreadPoolExecutor

# Maximum no. of parallel hits to get NAV history from MFAPI
_RESEARCH_MAX_WORKERS = 5

# ------------------------------------------------------------
# Research configuration
# ------------------------------------------------------------
# The application supplies the validated FinMan configuration
# during startup.  Research itself does not import or create
# application configuration.
# ------------------------------------------------------------
_RESEARCH_INDEX_FLNAME: Path | None = None
_RESEARCH_BENCHMARK_DIR: Path | None = None
_BENCHMARK_CACHE_LOCK = threading.Lock()

# ============================================================
# Configure the Research filesystem
# ============================================================
def configure_research(config, base_dir: Path):
    """
    Configure the Research subsystem using the application's validated FinMan configuration.

    The research cache path is stored in config.cache.research.
    Relative paths are resolved against the FinMan base directory.
    This function is called before calling the research sub-system for the first time
    """
    global _RESEARCH_INDEX_FLNAME, _RESEARCH_BENCHMARK_DIR

    _RESEARCH_INDEX_FLNAME = (base_dir / config.cache.research).resolve()
    _RESEARCH_BENCHMARK_DIR = _RESEARCH_INDEX_FLNAME.parent / "benchmark_history"
    _RESEARCH_BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# Cache Paths
# ============================================================
def _get_research_index_flname() -> Path:
    """
    Return the configured Research index cache file name.
    """
    if _RESEARCH_INDEX_FLNAME is None:
        raise RuntimeError("Research subsystem has not been configured")

    return _RESEARCH_INDEX_FLNAME

def _benchmark_cache_path(benchmark: str) -> Path:
    if _RESEARCH_BENCHMARK_DIR is None:
        raise RuntimeError("Research is not configured")

    return _RESEARCH_BENCHMARK_DIR / f"{benchmark}.json"

# ============================================================
# Benchmark cache
# ============================================================
def _load_benchmark_history(benchmark: str) -> BenchmarkHistory | None:
    path = _benchmark_cache_path(benchmark)

    if not path.exists():
        return None

    try:
        data = read_json(path)
        if not data:
            return None

        return BenchmarkHistory.model_validate(data)

    except Exception:
        logger.exception(
            "Failed to load benchmark cache: %s",
            path,
        )
        return None

def _save_benchmark_history(history: BenchmarkHistory):
    path = _benchmark_cache_path(history.benchmark)

    path.parent.mkdir(parents=True, exist_ok=True)

    write_json(
        path,
        history.model_dump(mode="json"),
    )

def _ensure_benchmark_history(
    benchmark: str,
    source_name: str,
    start_date: date,
    end_date: date,
) -> BenchmarkHistory:
    with _BENCHMARK_CACHE_LOCK:
        cached = _load_benchmark_history(benchmark)

        if cached is None:
            df = fetch_nifty_tri(source_name, start_date, end_date)

            points = [
                BenchmarkPoint(date=row["Date"].date()
                    if hasattr(row["Date"], "date")
                    else row["Date"],
                    tri=float(row["TRI"]),
                )
                for _, row in df.iterrows()
            ]

            result = BenchmarkHistory(benchmark=benchmark, requested_start=start_date,
                                      requested_end=end_date, history=points)

            _save_benchmark_history(result)
            return result

        # Already covered by the cached request range.
        if (cached.requested_start is not None and cached.requested_end is not None
            and cached.requested_start <= start_date and cached.requested_end >= end_date):
            return cached

        frames = []

        if cached.requested_start is None or start_date < cached.requested_start:
            left_end = (
                cached.requested_start
                if cached.requested_start is not None
                else end_date
            )

            df = fetch_nifty_tri(source_name, start_date, left_end)

            frames.append(df)

        if cached.requested_end is None or end_date > cached.requested_end:
            right_start = (
                cached.requested_end
                if cached.requested_end is not None
                else start_date
            )

            df = fetch_nifty_tri(source_name, right_start, end_date)

            frames.append(df)

        points_by_date = {
            point.date: point
            for point in cached.history
        }

        for df in frames:
            for _, row in df.iterrows():
                row_date = (
                    row["Date"].date()
                    if hasattr(row["Date"], "date")
                    else row["Date"]
                )

                points_by_date[row_date] = BenchmarkPoint(date=row_date, tri=float(row["TRI"]))

        new_start = (
            min(cached.requested_start, start_date)
            if cached.requested_start is not None
            else start_date
        )

        new_end = (
            max(cached.requested_end, end_date)
            if cached.requested_end is not None
            else end_date
        )

        result = BenchmarkHistory(benchmark=benchmark, requested_start=new_start,requested_end=new_end,
                                  history=sorted(points_by_date.values(), key=lambda p: p.date) )

        _save_benchmark_history(result)

        return result

# ============================================================
# Benchmark lookup
# ============================================================
@lru_cache(maxsize=1)
def _get_benchmark_map():
    """
    Build and cache a lookup table of AMFI code -> benchmark.

    load_mf_master() contains the complete mutual-fund master data. Research refresh processes 
    multiple funds, so loading and scanning the master data for every fund would repeat the same work.
    The cache means:
        - load_mf_master() is called only once per application lifetime (until the cache is cleared).
        - Each benchmark lookup is an O(1) dictionary lookup.
        - All research worker threads reuse the same cached map.

    Example:
        {
            "118955": "NIFTY_500_TRI",
            "118968": "NIFTY_500_TRI",
            ...
        }

    If the underlying MF master data is changed while the application is running, call:
        _get_benchmark_map.cache_clear()
    This invalidates the cached dictionary. The next call to
    _get_benchmark_map() will reload the master data and rebuild it.
    """
    mf_master = load_mf_master()

    return {
        str(entry["amfi_code"]): entry.get("benchmark")
        for entry in mf_master.values()
        if entry.get("amfi_code")
    }

def _get_benchmark_for_amfi(amfi_code: str) -> str | None:
    """
    Return the benchmark configured for an AMFI code.

    This function deliberately hides the implementation of the benchmark lookup from callers. Callers 
    only need to provide the AMFI code; the cached benchmark map is handled internally.
    """
    return _get_benchmark_map().get(str(amfi_code))

@lru_cache(maxsize=1)
def _get_benchmark_definition(benchmark_key: str) -> dict | None:
    """
    Return the benchmark definition from the central benchmark catalogue.
    """
    return load_benchmark_master().get(benchmark_key)

# ============================================================
# Research calculations
# ============================================================
def _calculate_benchmark_comparison(
    fund_history: NAVHistory,
    benchmark_key: str,
    fund_metrics: dict,
) -> BenchmarkComparison | None:

    benchmark = _get_benchmark_definition(benchmark_key)

    if not benchmark:
        logger.debug(
            "Benchmark %s is not configured in benchmark catalogue",
            benchmark_key,
        )
        return None

    provider = benchmark.get("provider")
    source_name = benchmark.get("source_name")

    if provider != "nifty_tri":
        logger.debug(
            "Benchmark %s uses unsupported provider: %s",
            benchmark_key,
            provider,
        )
        return None

    if not source_name:
        logger.debug(
            "Benchmark %s has no source_name configured",
            benchmark_key,
        )
        return None

    fund_dates = [
        point.date
        for point in fund_history.history
    ]

    if not fund_dates:
        return None

    start_date = min(fund_dates)
    end_date = max(fund_dates)

    benchmark_history = _ensure_benchmark_history(
        benchmark_key,
        source_name,
        start_date,
        end_date,
    )

    bh = benchmark_history.history

    benchmark_return_1m = calc_trailing_return_months(bh, 1)
    benchmark_return_3m = calc_trailing_return_months(bh, 3)
    benchmark_return_6m = calc_trailing_return_months(bh, 6)

    benchmark_cagr_1y = calc_trailing_cagr(bh, 1)
    benchmark_cagr_3y = calc_trailing_cagr(bh, 3)
    benchmark_cagr_5y = calc_trailing_cagr(bh, 5)
    benchmark_cagr_7y = calc_trailing_cagr(bh, 7)
    benchmark_cagr_10y = calc_trailing_cagr(bh, 10)

    def diff(fund_value, benchmark_value):
        if fund_value is None or benchmark_value is None:
            return None
        return fund_value - benchmark_value

    return BenchmarkComparison(
        benchmark=benchmark_key,
        benchmark_name=benchmark.get("display_name"),

        benchmark_return_1m=benchmark_return_1m,
        benchmark_return_3m=benchmark_return_3m,
        benchmark_return_6m=benchmark_return_6m,

        benchmark_cagr_1y=benchmark_cagr_1y,
        benchmark_cagr_3y=benchmark_cagr_3y,
        benchmark_cagr_5y=benchmark_cagr_5y,
        benchmark_cagr_7y=benchmark_cagr_7y,
        benchmark_cagr_10y=benchmark_cagr_10y,

        outperformance_1m=diff(fund_metrics["return_1m"], benchmark_return_1m),
        outperformance_3m=diff(fund_metrics["return_3m"], benchmark_return_3m),
        outperformance_6m=diff(fund_metrics["return_6m"], benchmark_return_6m),
        outperformance_1y=diff(fund_metrics["cagr_1y"], benchmark_cagr_1y),
        outperformance_3y=diff(fund_metrics["cagr_3y"], benchmark_cagr_3y),
        outperformance_5y=diff(fund_metrics["cagr_5y"], benchmark_cagr_5y),
        outperformance_7y=diff(fund_metrics["cagr_7y"], benchmark_cagr_7y),
        outperformance_10y=diff(fund_metrics["cagr_10y"], benchmark_cagr_10y),

        benchmark_rolling_1m=calc_rolling_returns_months(benchmark_history, 1),
        benchmark_rolling_3m=calc_rolling_returns_months(benchmark_history, 3),
        benchmark_rolling_6m=calc_rolling_returns_months(benchmark_history, 6),

        benchmark_rolling_1y=calc_rolling_returns(benchmark_history, 1),
        benchmark_rolling_3y=calc_rolling_returns(benchmark_history, 3),
        benchmark_rolling_5y=calc_rolling_returns(benchmark_history, 5),
        benchmark_rolling_7y=calc_rolling_returns(benchmark_history, 7),
        benchmark_rolling_10y=calc_rolling_returns(benchmark_history, 10),

        rolling_outperformance_1m=calc_rolling_outperformance(fund_history,benchmark_history,months=1),
        rolling_outperformance_3m=calc_rolling_outperformance(fund_history,benchmark_history,months=3),
        rolling_outperformance_6m=calc_rolling_outperformance(fund_history,benchmark_history,months=6),

        rolling_outperformance_1y=calc_rolling_outperformance(fund_history,benchmark_history,years=1),
        rolling_outperformance_3y=calc_rolling_outperformance(fund_history,benchmark_history,years=3),
        rolling_outperformance_5y=calc_rolling_outperformance(fund_history,benchmark_history,years=5),
        rolling_outperformance_7y=calc_rolling_outperformance(fund_history,benchmark_history,years=7),
        rolling_outperformance_10y=calc_rolling_outperformance(fund_history,benchmark_history,years=10),
    )

def _calculate_research(amfi_code:str) -> FundResearch:
    # Download NAV history and calculate all research metrics for one amfi_code
    history = download_nav_history(amfi_code)
    save_nav_history(history)

    fund_metrics = {
        "return_1m": calc_trailing_return_months(history.history, 1),
        "return_3m": calc_trailing_return_months(history.history, 3),
        "return_6m": calc_trailing_return_months(history.history, 6),
        "cagr_1y": calc_trailing_cagr(history.history, 1),
        "cagr_3y": calc_trailing_cagr(history.history, 3),
        "cagr_5y": calc_trailing_cagr(history.history, 5),
        "cagr_7y": calc_trailing_cagr(history.history, 7),
        "cagr_10y": calc_trailing_cagr(history.history, 10),
    }        

    benchmark_comparison = None
    benchmark_key = _get_benchmark_for_amfi(amfi_code)
    if benchmark_key:
        try:
            benchmark_comparison = _calculate_benchmark_comparison(history, benchmark_key, fund_metrics)
        except Exception:
            logger.exception("Benchmark comparison failed for AMFI %s benchmark=%s",
                             amfi_code, benchmark_key)

    research = FundResearch(
        amfi_code=history.amfi_code,
        fund_name=history.fund_name,

        return_1m=fund_metrics["return_1m"],
        return_3m=fund_metrics["return_3m"],
        return_6m=fund_metrics["return_6m"],
        cagr_1y=fund_metrics["cagr_1y"],
        cagr_3y=fund_metrics["cagr_3y"],
        cagr_5y=fund_metrics["cagr_5y"],
        cagr_7y=fund_metrics["cagr_7y"],
        cagr_10y=fund_metrics["cagr_10y"],

        rolling_1m=calc_rolling_returns_months(history, 1),
        rolling_3m=calc_rolling_returns_months(history, 3),
        rolling_6m=calc_rolling_returns_months(history, 6),
        rolling_1y=calc_rolling_returns(history, 1),
        rolling_3y=calc_rolling_returns(history, 3),
        rolling_5y=calc_rolling_returns(history, 5),
        rolling_7y=calc_rolling_returns(history, 7),
        rolling_10y=calc_rolling_returns(history, 10),        

        drawdown=calc_drawdown(history),

        volatility=calc_volatility(history),

        calendar_returns=calc_calendar_year_returns(history),

        benchmark=benchmark_comparison,
    )

    return research

# ============================================================
# Research function to load/GET/update the JSON index file
# ============================================================
def load_research_index() -> ResearchIndex:
    # Loads the research index JSON file and returns it as a ResearchIndex model.
    # The index contains the research results for all funds that have been processed.
    research_index_flname = _get_research_index_flname()
    data = read_json(research_index_flname)

    if data is None:
        # If there is not data return an empty ResearchIndex data
        return ResearchIndex(generated_at=datetime.now(),funds={}        )
    
    return ResearchIndex.model_validate(data)

def load_research(amfi_code: str) -> FundResearch | None:
    # Get the research for a specific fund
    index = load_research_index()

    return index.funds.get(amfi_code)

def _update_research_index(research_index: ResearchIndex, research: FundResearch) -> None:
    # Update the in-memory research index with the latest fund research.
    research_index.generated_at = datetime.now()
    research_index.funds[research.amfi_code] = research

def _save_research_index(research_index: ResearchIndex) -> Path:
    # Persists the data, saves the in-moemory date to file
    research_index_file = _get_research_index_flname()
    write_json(research_index_file, research_index.model_dump(mode="json"))
    logger.debug("Research index saved: %s (%d funds)", research_index_file, len(research_index.funds))
    return research_index_file

# ============================================================
# Single-fund research
# ============================================================
def _refresh_research(amfi_code:str) -> tuple[FundResearch, Path]:
    # Complete single-fund research refresh orchestration

    # Load the current index, update it in memory, then save it once.
    index = load_research_index()
    # Calculate the research, update the index in memory, and save the updated research index.
    research = _calculate_research(amfi_code)
    # Update the in-memory index with the latest research computation
    _update_research_index(index, research)
    # Save the index JSON back to file
    cache_file = _save_research_index(index)    

    return (research, cache_file)

def get_research(amfi_code: str, refresh: bool = False) -> FundResearch:
    # Returns research for a specific AMFI code.
    # refresh=True forces a new NAV download and research calculation.
    # Otherwise, cached research is returned when available;
    # if no cached research exists, the research is generated.
    if refresh:
        research, _ = _refresh_research(amfi_code)
        return research

    research = load_research(amfi_code)   # Get the reseach for the amfi_code from cache
    if research:
        return research
    
    research, _ = _refresh_research(amfi_code)

    return research

# ============================================================
# Research refresh
# ============================================================
def _missing_metrics(research: FundResearch) -> list[str]:
    missing = []

    if research.cagr_1y is None:
        missing.append("1Y CAGR")
    if research.cagr_3y is None:
        missing.append("3Y CAGR")
    if research.cagr_5y is None:
        missing.append("5Y CAGR")
    if research.cagr_7y is None:
        missing.append("7Y CAGR")
    if research.cagr_10y is None:
        missing.append("10Y CAGR")

    if research.rolling_1y is None:
        missing.append("1Y rolling")
    if research.rolling_3y is None:
        missing.append("3Y rolling")
    if research.rolling_5y is None:
        missing.append("5Y rolling")
    if research.rolling_7y is None:
        missing.append("7Y rolling")
    if research.rolling_10y is None:
        missing.append("10Y rolling")

    return missing

def refresh_all_research(amfi_codes: list[str]) -> RefreshAllResult:
    """
    Complete multi-fund research refresh orchestration
    Refresh research for all  AMFI code and track successes and failures.

    Each fund is independent, so NAV download and metric calculation
    are performed concurrently using a bounded thread pool.

    The ResearchIndex itself is updated only by the main thread and
    is saved once after all funds have completed.
    """    
    success = 0
    failed = 0
    benchmark_available = 0
    benchmark_unavailable = 0

    successful_amfi_codes = []
    failed_amfi_codes = []

    total = len(amfi_codes)
    logger.info("Research refresh started: %d funds", total)

    # Load the research index JSON once.
    index = load_research_index()
    insufficient_history = {}

    # ------------------------------------------------------------------
    # Parallel research refresh
    #
    # Each mutual fund can be downloaded and calculated independently. The expensive part of 
    # _calculate_research() is mainly the external MFAPI request, so we use a small pool of worker 
    # threads to process several funds at the same time.
    #
    # max_workers=5 means at most 5 research calculations are running concurrently.
    # The remaining jobs wait in the executor's queue.
    #
    # There are TWO loops below, and they have different purposes:
    #   1. FIRST LOOP
    #      Submit all 17 fund calculations to the executor.
    #
    #      executor.submit() does NOT wait for the calculation to finish. It starts the job in a
    #      worker thread (or queues it if all workers are busy) and immediately returns a Future object.
    #
    #      'futures' is simply a dictionary:
    #          AMFI code -> Future representing that fund's calculation
    #      Example:
    #          futures["118955"] -> Future for HDFC Flexi Cap
    #   2. SECOND LOOP
    #      Go through the funds again and collect their results.
    #
    #      future.result() waits only if that particular fund has not finished yet. Once the result 
    #      is available, the main thread processes it and then moves to the next fund.
    #
    #      IMPORTANT:
    #      The second loop is sequential, but the expensive _calculate_research() work is already 
    #      running concurrently in the worker threads.
    #
    #      In simple terms:
    #          First loop  = "Here are all the jobs. Go do them."
    #          Workers    = "We are doing up to 5 at a time."
    #          Second loop = "Give me the answers one by one."
    #
    # The ResearchIndex is deliberately updated only by the main thread.
    # This avoids multiple worker threads modifying/writing the same JSON index simultaneously.
    #
    # The index is saved ONCE after all funds have completed.
    # ------------------------------------------------------------------

    max_workers = min(_RESEARCH_MAX_WORKERS, total)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:

        # FIRST LOOP:
        # Submit all research jobs. This loop runs once per fund.
        # It does not wait for each research calculation to finish.
        futures = {}

        for amfi_code in amfi_codes:
            future = executor.submit(
                _calculate_research,
                amfi_code
            )
            futures[amfi_code] = future

        # SECOND LOOP:
        # Collect the results. This also runs once per fund.
        #
        # The worker threads are doing the actual research calculations
        # while this main thread collects the completed results.
        for i, amfi_code in enumerate(amfi_codes, start=1):

            future = futures[amfi_code]

            try:
                # Wait for this particular fund if it is not finished yet.
                research = future.result()

                missing = _missing_metrics(research)

                if missing:
                    insufficient_history[research.fund_name] = missing

                # Only the main thread updates the shared ResearchIndex.
                _update_research_index(index, research)

                success += 1

                if research.benchmark is not None:
                    benchmark_available += 1
                else:
                    benchmark_unavailable += 1
                successful_amfi_codes.append(amfi_code)

            except Exception as ex:
                failed += 1
                failed_amfi_codes.append(amfi_code)

                logger.exception(
                    "Research refresh failed [%d/%d]: %s - %s",
                    i,
                    total,
                    amfi_code,
                    ex
                )

    # Save the index once after all funds have been processed.
    if success > 0:
        _save_research_index(index)

    logger.info(
        "Research refresh complete: %d/%d successful, %d failed",
        success,
        total,
        failed,
    )
    logger.info(
        "Research benchmark comparison: %d available, %d unavailable",
        benchmark_available,
        benchmark_unavailable,
    )
    if insufficient_history:
        details = "; ".join(
            f"{fund}: {', '.join(metrics)}"
            for fund, metrics in insufficient_history.items()
        )
        logger.info("Research metrics unavailable for %d funds: %s", len(insufficient_history), details)
    if failed:
        logger.warning("Research refresh failed AMFI codes: %s", ", ".join(failed_amfi_codes))
    return RefreshAllResult(
        total=total,
        success=success,
        failed=failed,
        benchmark_available=benchmark_available,
        benchmark_unavailable=benchmark_unavailable,
        insufficient_history=len(insufficient_history),
        successful_amfi_codes=successful_amfi_codes,
        failed_amfi_codes=failed_amfi_codes,
    )

def run_research_refresh():
    # # Application level research refresh orchestration
    # Helper function to orchestrate the calling of research refresh 

    mf_master = load_mf_master()
    amfi_codes = [
        str(entry["amfi_code"])
        for entry in mf_master.values()
        if entry.get("amfi_code")
    ]

    logger.info("Research refresh requested for %d mutual funds", len(amfi_codes))

    if amfi_codes:
        return refresh_all_research(amfi_codes)
    
    logger.info("Research refresh: no mutual funds found")
    return None


