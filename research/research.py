from datetime import datetime
from pathlib import Path

import logging
logger = logging.getLogger("finman")

from research.nav_history import NAVHistory, FundResearch, ResearchIndex, RefreshAllResult

from research.download_nav_history import (
    download_nav_history,
    save_nav_history
)

from research.nav_metrics import (
    calc_trailing_cagr,
    calc_rolling_returns,
    calc_drawdown,
    calc_calendar_year_returns,
    calc_volatility,
)

from ingestion.json_io import read_json, write_json

from market.external_links import load_mf_master

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

#----------------------------------------------------
# Configure the Research filesystem
#----------------------------------------------------
def configure_research(config, base_dir: Path):
    """
    Configure the Research subsystem using the application's validated FinMan configuration.

    The research cache path is stored in config.cache.research.
    Relative paths are resolved against the FinMan base directory.
    This function is called before calling the research sub-system for the first time
    """
    global _RESEARCH_INDEX_FLNAME

    _RESEARCH_INDEX_FLNAME = (base_dir / config.cache.research).resolve()

#----------------------------------------------------
#Special function to ensure that in case there is no history beyond a period it is managed properly
#----------------------------------------------------
def _safe_rolling_returns(nav_history: NAVHistory, years: int):
    # Return None instead of failing when there is insufficient NAV history.
    try:
        return calc_rolling_returns(nav_history,years)
    except ValueError:
        # logger.warning("Insufficient history for %sY rolling returns: %s [%s]", years, nav_history.fund_name, nav_history.amfi_code)
        return None

#Special function to ensure that in case there is no history beyond a period it is managed properly
def _safe_trailing_cagr(nav_history: NAVHistory, years: int):
    # Return None instead of failing when there is insufficient NAV history.
    try:
        return calc_trailing_cagr(nav_history.history, years)
    except ValueError:
        # logger.warning("Insufficient history for %sY CAGR: %s [%s]", years, nav_history.fund_name, nav_history.amfi_code)
        return None
    
#----------------------------------------------------
# Internal functions
#----------------------------------------------------
def _get_research_index_flname() -> Path:
    """
    Return the configured Research index cache file name.
    """
    if _RESEARCH_INDEX_FLNAME is None:
        raise RuntimeError("Research subsystem has not been configured")

    return _RESEARCH_INDEX_FLNAME
    
def _calculate_research(amfi_code:str) -> FundResearch:
    # Download NAV history and calculate all research metrics for one amfi_code
    history = download_nav_history(amfi_code)
    save_nav_history(history)

    research = FundResearch(
        amfi_code=history.amfi_code,
        fund_name=history.fund_name,

        cagr_1y=_safe_trailing_cagr(history,1),
        cagr_3y=_safe_trailing_cagr(history,3),
        cagr_5y=_safe_trailing_cagr(history,5),

        rolling_1y=_safe_rolling_returns(history,1),
        rolling_3y=_safe_rolling_returns(history,3),
        rolling_5y=_safe_rolling_returns(history,5),

        drawdown=calc_drawdown(history),

        volatility=calc_volatility(history),

        calendar_returns=calc_calendar_year_returns(history),
    )

    def pct(value):
        return f"{value * 100:.2f}%" if value is not None else "N/A"

    # logger.debug("Research: %s [%s] - CAGR 1Y=%s 3Y=%s 5Y=%s", research.fund_name, research.amfi_code,
    #             pct(research.cagr_1y), pct(research.cagr_3y), pct(research.cagr_5y))

    # logger.debug("Research: %s [%s] - Drawdown max=%s current=%s | Volatility=%s (%d obs)", research.fund_name,
    #             research.amfi_code, pct(research.drawdown.max_drawdown), pct(research.drawdown.current_drawdown),
    #             pct(research.volatility.volatility), research.volatility.observations)

    # logger.debug("Research: %s [%s] - Rolling observations: 1Y=%s 3Y=%s 5Y=%s", research.fund_name,
    #             research.amfi_code, research.rolling_1y.observations if research.rolling_1y else "N/A",
    #             research.rolling_3y.observations if research.rolling_3y else "N/A",
    #             research.rolling_5y.observations if research.rolling_5y else "N/A")

    # logger.debug("Research: %s [%s] - %d calendar-year returns", research.fund_name, research.amfi_code,
    #             len(research.calendar_returns))

    # logger.debug("Research: completed %s [%s]", research.fund_name, research.amfi_code)

    return research

    return research

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

#----------------------------------------------------
# Research function to load/GET/update the JSON index file
#----------------------------------------------------
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

def _missing_metrics(research: FundResearch) -> list[str]:
    missing = []

    if research.cagr_1y is None:
        missing.append("1Y CAGR")
    if research.cagr_3y is None:
        missing.append("3Y CAGR")
    if research.cagr_5y is None:
        missing.append("5Y CAGR")

    if research.rolling_1y is None:
        missing.append("1Y rolling")
    if research.rolling_3y is None:
        missing.append("3Y rolling")
    if research.rolling_5y is None:
        missing.append("5Y rolling")

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

    logger.info("Research refresh complete: %d/%d successful, %d failed", success, total, failed)
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

