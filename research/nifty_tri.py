from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd
import requests


TRI_URL = "https://www.niftyindices.com/Backpage/getTotalReturnIndexString"

HEADERS = {
    "Content-Type": "application/json; charset=UTF-8",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.niftyindices.com/reports/historical-data",
    "Origin": "https://www.niftyindices.com",
    "X-Requested-With": "XMLHttpRequest",
}


# FinMan benchmark name -> Nifty index name
NIFTY_TRI_INDEXES = {
    "NIFTY_MIDCAP150_TRI": "NIFTY MIDCAP 150",
    "NIFTY50_TRI": "NIFTY 50",
    "NIFTY500_TRI": "NIFTY 500",
    "NIFTY_SMALLCAP250_TRI": "NIFTY SMALLCAP 250",
    "NIFTY_LARGEMIDCAP250_TRI": "NIFTY LARGE MIDCAP 250",
}


NIFTY_TRI_DISPLAY_NAMES = {
    "NIFTY50_TRI": "NIFTY 50 TRI",
    "NIFTY500_TRI": "NIFTY 500 TRI",
    "NIFTY_SMALLCAP250_TRI": "NIFTY Smallcap 250 TRI",
    "NIFTY_LARGEMIDCAP250_TRI": "NIFTY LargeMidcap 250 TRI",
    "NIFTY_MIDCAP150_TRI": "NIFTY Midcap 150 TRI",
}

def is_supported_nifty_tri(benchmark: str) -> bool:
    return benchmark in NIFTY_TRI_INDEXES

def fetch_nifty_tri(benchmark: str, start_date: date, end_date: date, chunk_days: int = 350) -> pd.DataFrame:

    if benchmark not in NIFTY_TRI_INDEXES:
        raise ValueError(f"Unsupported Nifty TRI benchmark: {benchmark}")

    index_name = NIFTY_TRI_INDEXES[benchmark]

    frames = []
    current = start_date

    while current <= end_date:

        chunk_end = min(current + timedelta(days=chunk_days), end_date)

        parameters = {
            "name": index_name,
            "startDate": current.strftime("%d-%b-%Y"),
            "endDate": chunk_end.strftime("%d-%b-%Y"),
            "indexName": index_name,
        }

        payload = json.dumps({"cinfo": json.dumps(parameters)})

        response = requests.post(TRI_URL, data=payload, headers=HEADERS, timeout=30)

        response.raise_for_status()

        # Nifty returns JSON but currently reports text/html.
        data = json.loads(response.text)

        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected TRI response for {benchmark}")

        if data:
            df = pd.DataFrame(data)

            df["Date"] = pd.to_datetime(df["Date"], format="%d %b %Y", errors="coerce")

            df["TRI"] = pd.to_numeric(df["TotalReturnsIndex"], errors="coerce",)

            df = df[["Date", "TRI"]].dropna()

            frames.append(df)

        current = chunk_end + timedelta(days=1)

    if not frames:
        return pd.DataFrame(columns=["Date", "TRI"])

    result = pd.concat(frames, ignore_index=True)

    return (
        result
        .drop_duplicates("Date")
        .sort_values("Date")
        .reset_index(drop=True)
    )