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


def fetch_nifty_tri(
    source_name: str,
    start_date: date,
    end_date: date,
    chunk_days: int = 350,
) -> pd.DataFrame:

    index_name = source_name

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
            raise RuntimeError(f"Unexpected TRI response for {source_name}")

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