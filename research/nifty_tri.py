"""
nifty_tri.py
------------
Utility module to fetch historical Total Return Index (TRI) data for Nifty indices 
from the official Nifty Indices website (niftyindices.com).

This module handles:
1. API payload formatting and request headers (spoofing browser metadata).
2. Date range chunking to prevent API timeout/truncation issues.
3. Parsing JSON responses into clean, formatted Pandas DataFrames.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd
import requests

# Backend endpoint used by niftyindices.com to retrieve historical TRI values
TRI_URL = "https://www.niftyindices.com/Backpage/getTotalReturnIndexString"

# Headers required by the Nifty Indices API to emulate a valid browser request.
# Without these headers (especially Referer and User-Agent), requests may be blocked.
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

def fetch_nifty_tri(source_name: str, start_date: date, end_date: date, 
                    chunk_days: int = 350) -> pd.DataFrame:
    """
    Fetches historical Total Return Index (TRI) data for a given Nifty index.

    Querying large date ranges in a single API call can cause timeouts or response 
    truncation. This function splits the date range into smaller chunks (default ~350 days),
    queries the Nifty API sequentially, and combines the results.

    Args:
        source_name (str): The exact name of the Nifty index (e.g., 'NIFTY 50').
        start_date (date): The start date for historical data retrieval.
        end_date (date): The end date for historical data retrieval.
        chunk_days (int, optional): Maximum number of days per API request chunk. 
            Defaults to 350.

    Returns:
        pd.DataFrame: A DataFrame containing two columns:
            - 'Date' (datetime64[ns]): The trading date sorted chronologically.
            - 'TRI' (float64): The Total Return Index value for that date.

    Raises:
        requests.HTTPError: If the HTTP request to the server fails.
        RuntimeError: If the API response format is not the expected list structure.
    """    
    index_name = source_name

    frames = []
    current = start_date

    # Iterate through the requested date range in smaller date blocks (chunks)
    while current <= end_date:
        # Determine the end date for the current chunk
        chunk_end = min(current + timedelta(days=chunk_days), end_date)

        # Build parameters as expected by the backend service.
        # Nifty API requires dates in 'DD-Mon-YYYY' format (e.g., '01-Jan-2025').
        parameters = {
            "name": index_name,
            "startDate": current.strftime("%d-%b-%Y"),
            "endDate": chunk_end.strftime("%d-%b-%Y"),
            "indexName": index_name,
        }

        # The API expects a double-serialized JSON payload nested under 'cinfo'
        payload = json.dumps({"cinfo": json.dumps(parameters)})

        # Make the POST request to the API
        response = requests.post(TRI_URL, data=payload, headers=HEADERS, timeout=30)
        response.raise_for_status()

        # The Nifty endpoint returns valid JSON string, though Content-Type header 
        # may state text/html.        
        data = json.loads(response.text)

        # Validate that the returned payload is a JSON list (array of records)
        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected TRI response for {source_name}")

        if data:
            df = pd.DataFrame(data)

            # Convert string dates (e.g., "01 Jan 2025") to Pandas Datetime objects
            df["Date"] = pd.to_datetime(df["Date"], format="%d %b %Y", errors="coerce")

            # Convert the raw index value column to numeric floats
            df["TRI"] = pd.to_numeric(df["TotalReturnsIndex"], errors="coerce",)

            # Select only target columns and drop rows where parsing failed
            df = df[["Date", "TRI"]].dropna()

            frames.append(df)

        # Move to the start of the next chunk period
        current = chunk_end + timedelta(days=1)

    # Return an empty DataFrame with expected schema if no records were found
    if not frames:
        return pd.DataFrame(columns=["Date", "TRI"])

    # Combine all individual chunk DataFrames into a single DataFrame
    result = pd.concat(frames, ignore_index=True)

    # Clean up the aggregated DataFrame: remove duplicate dates, sort chronologically,
    # and reset the row index.
    return (
        result
        .drop_duplicates("Date")
        .sort_values("Date")
        .reset_index(drop=True)
    )