from pathlib import Path
from datetime import datetime
import requests
import time

import logging
logger = logging.getLogger(__name__)

from research.nav_history import NAVHistory, NAVPoint

BASE_DIR = Path(__file__).resolve().parent.parent
NAV_CACHE_DIR = BASE_DIR / "cache" / "research" / "nav_history"
BENCHMARK_CACHE_DIR = BASE_DIR / "cache" / "research" / "benchmark_history"

class NAVDownloadError(Exception):
    pass

def download_nav_history(amfi_code: str) -> NAVHistory:
    """
    Download historical NAV data from MFAPI. And convert all the MFAPI rows to NAVPoint schema type.
    """
    url = f"https://api.mfapi.in/mf/{amfi_code}"

    for attempt in range(3):
        try:
            logger.debug(f"Downloading: {url} (attempt={attempt+1})")
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            logger.debug("Downloaded AMFI %s in attempt %d", amfi_code, attempt + 1,)
            data = response.json()
            break
        except requests.exceptions.HTTPError as http_err:
            raise NAVDownloadError(f"HTTP error occurred while downloading AMFI {amfi_code}: {http_err}") from http_err
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError,) as ex:
            logger.warning("Network error downloading %s (attempt=%d/3): %s", amfi_code, attempt + 1, ex,)
            if attempt == 2:
                raise NAVDownloadError(f"Network error downloading AMFI {amfi_code}: {ex}") from ex
            time.sleep(2 ** attempt)   # Exponential backoff: 1s, 2s, 4s
        except requests.exceptions.RequestException as ex:
            raise NAVDownloadError(f"Request failed downloading AMFI {amfi_code}: {ex}") from ex

    # Extract the schem name for the fund - form the AMFI code passed
    fund_name = data["meta"]["scheme_name"]

    history = []

    # Convert each of the API row into a NAVPoint scheme type
    for row in data["data"]:
        try:
            history.append( NAVPoint(date=datetime.strptime(row["date"], "%d-%m-%Y").date(), nav=float(row["nav"])) )
        except Exception as ex:
            logger.warning("Skipping invalid NAV row for AMFI %s: row=%s error=%s", amfi_code, row, ex)
            continue

    # if logger.isEnabledFor(logging.DEBUG):
    #     import json
    #     logger.debug("EXAMINING THE METADATA IN THE API\n%s",
    #                 json.dumps(data["meta"], indent=2))

    return NAVHistory(amfi_code=amfi_code, fund_name=fund_name, history=history)

def save_nav_history(nav_history: NAVHistory, type="nav"):
    # Writes the provided NAVHistory object to {amfi_code}.json
    if type == "nav":
        CACHE_DIR = NAV_CACHE_DIR     
    elif type == "benchmark":
        CACHE_DIR = BENCHMARK_CACHE_DIR

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    file_path = CACHE_DIR / f"{nav_history.amfi_code}.json"

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            # json.dump(nav_history.model_dump(), f, indent=2, ensure_ascii=False)
            f.write(nav_history.model_dump_json(indent=2))
    except OSError as ex:
        raise RuntimeError(f"Failed writing cache file {file_path}") from ex

    return file_path
