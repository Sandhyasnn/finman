"""
Generic Excel reader for FinMan.

This module knows only how to read a configured Excel worksheet.
It has no knowledge of stocks, mutual funds, smallcases, transactions,
tax, or normalized FinMan models.

The reader returns raw worksheet rows as dictionaries keyed by the
Excel header names. Interpretation of those values belongs to the
mapping layer.
"""
from pathlib import Path
from typing import Any
from logger import logger

from openpyxl import load_workbook

def read_excel_rows(excel_file: str | Path, sheet_name: str, header_row: int,) -> list[dict[str, Any]]:
    """
    Read a configured Excel worksheet and return its non-empty rows as dictionaries keyed by the worksheet headers.
    Uses the configured header row and does not apply FinMan business rules, mappings, filtering, or validation.
    Args:
        excel_file: Path to the Excel workbook.
        sheet_name: Worksheet containing the data.
        header_row: One-based Excel row containing the column headers.
    Returns:
        A list of dictionaries. Each dictionary represents one
        non-empty worksheet row and uses the Excel headers as keys.
    Raises:
        FileNotFoundError: If the workbook does not exist.
        ValueError: If the sheet/header configuration is invalid.

    The reader does not apply business rules, mappings, filters, transformations, or Pydantic validation.
    """    
    logger.debug("Reading Excel file '%s', sheet '%s', header row %d", excel_file, sheet_name, header_row,)
    path = Path(excel_file)

    if not path.exists():
        raise FileNotFoundError(f"Excel file not found: {path}")

    if header_row < 1:
        raise ValueError("header_row must be >= 1")

    workbook = None
    # data_only=True returns Excel's cached formula results rather than  formula expressions. 
    # FinMan deliberately does not evaluate Excel formulas itself
    try:
        workbook = load_workbook(filename=path, data_only=True, read_only=True,)
    except PermissionError:
            # Triggered when Excel or another process has an exclusive lock on the file
            message = (f"Excel file '{path}' is currently open in Excel or another program. "
                f"Please close the file and try again.")
            logger.error(message)
            # Or raise a custom application error:
            # raise FinManError(f"File '{path}' is locked by another process.")
            raise ValueError(message) from None    
    except InvalidFileException:
        message = (
            f"Excel file '{path}' is currently open in Excel or another program. "
            f"Please close the file and try again."
        )
        logger.error(message)
        raise ValueError(message) from None
    except FileNotFoundError:
        message = (
            f"Excel file '{path}' is could ot be found. "
            f"Please save the file in the correct location or adjust config."
        )
        logger.error(message)
        raise ValueError(message) from None
    except Exception as err:
        message = (f"Unexpected error loading workbook {path}: {err}")
        logger.error(message)
        raise ValueError(message) from None

    try:
        if sheet_name not in workbook.sheetnames:
            raise ValueError(f"Sheet '{sheet_name}' not found in {path}. "
                f"Available sheets: {workbook.sheetnames}")

        worksheet = workbook[sheet_name]

        rows = worksheet.iter_rows(min_row=header_row, values_only=True,)

        headers = next(rows, None)

        if headers is None:
            return []

        # logger.debug("Excel headers: %s", headers)   # Enable if there is a mismatch between excel headers and config data

        # Clean up header names and remember the positions of columns that have a usable header.
        valid_column_indexes = []
        clean_headers = []

        for index, header in enumerate(headers):
            if isinstance(header, str):
                header = header.strip()

            if header is not None and header != "":
                valid_column_indexes.append(index)
                clean_headers.append(header)

        headers = clean_headers
        if len(headers) != len(set(headers)):
            raise ValueError(f"Header row {header_row} contains duplicate column headers")

        result: list[dict[str, Any]] = []

        for values in rows:
            if not any(value is not None for value in values):
                continue
            # row = dict(zip(headers, values))
            # result.append(row)
            row = {}
            for position, header in enumerate(headers):
                original_index = valid_column_indexes[position]
                row[header] = values[original_index]
            result.append(row)
        logger.info("Read %d rows from Excel sheet '%s'", len(result), sheet_name,)
        return result

    finally:
        if workbook:
            workbook.close()