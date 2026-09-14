"""
FinMan consolidated investment ingestion.

Reads consolidated.xlsx, applies the configured row filter, maps the selected rows to ConsolidatedHolding 
records, validates them, and writes consolidated.json.

This is deliberately separate from transaction ingestion.
"""
from pathlib import Path
from pydantic import BaseModel

from ingestion.config import DatasetConfig
from ingestion.models import ConsolidatedHolding
from ingestion.excel_reader import read_excel_rows
from ingestion.mapper import (
    map_consolidated_row,
    validate_mapping,
    apply_row_filter,
    map_filter_values
)
from ingestion.json_io import read_json, write_json
from logger import logger


class ConsolidatedIngestionResult(BaseModel):
    dataset: str
    rows_read: int
    rows_filtered: int
    holdings_validated: int
    holdings: list[ConsolidatedHolding]

def _ingest_consolidated_dataset(dataset_name: str, dataset: DatasetConfig) -> ConsolidatedIngestionResult:
    logger.info("Starting consolidated ingestion for dataset '%s'", dataset_name,)

    rows = read_excel_rows(
        excel_file=dataset.excel_file,
        sheet_name=dataset.transactions.excel_sheet,
        header_row=dataset.transactions.header_row,
    )

    if not rows:
        raise ValueError(f"Dataset '{dataset_name}' contains no consolidated rows")

    headers = list(rows[0].keys())

    errors = validate_mapping(dataset.transactions, headers)

    if errors:
        raise ValueError(
            f"Consolidated mapping validation failed for "
            f"dataset '{dataset_name}':\n"
            + "\n".join(f"- {error}" for error in errors)
        )

    holdings = []
    rows_filtered = 0

    for row_number, row in enumerate(rows, start=1):
        try:
            # Map only the fields needed to evaluate the row filter.
            # This allows unwanted rows to be discarded before converting the remaining transaction fields.
            filter_values = map_filter_values(row, dataset.transactions, dataset.row_filter)            

            # Row filter still operates on the mapped transaction fields.
            if not apply_row_filter(filter_values, dataset.row_filter):
                rows_filtered += 1
                continue

            values = map_consolidated_row(row, dataset.transactions)


            holding = ConsolidatedHolding.model_validate(values)
            holdings.append(holding)

        except ValueError as exc:
            message = (f"Consolidated validation failed for dataset '{dataset_name}', row {row_number}: {exc} | Row:{row}")
            logger.error(message)
            raise ValueError(message) from exc

    logger.info("Validated %d consolidated holdings from dataset '%s'", len(holdings), dataset_name)

    return ConsolidatedIngestionResult(
        dataset=dataset_name,
        rows_read=len(rows),
        rows_filtered=rows_filtered,
        holdings_validated=len(holdings),
        holdings=holdings,
    )


def ingest_consolidated(dataset: DatasetConfig, output_file: str | Path) -> dict:
    output_path = Path(output_file)

    try:
        result = _ingest_consolidated_dataset("consolidateddata", dataset)

        write_json(
            output_path,
            [
                holding.model_dump(mode="json")
                for holding in result.holdings
            ],
        )

        logger.info("Wrote %d consolidated holdings to '%s'", len(result.holdings), output_path)

        return {
            "status": "ok",
            "dataset": result.dataset,
            "rows_read": result.rows_read,
            "rows_filtered": result.rows_filtered,
            "holdings_validated": result.holdings_validated,
            "output_file": str(output_path),
            "holdings": result.holdings,
        }

    except ValueError as exc:
        logger.error("Consolidated ingestion failed: %s", exc,)

        if output_path.exists():
            old_holdings = read_json(output_path, default=[],)

            holdings = [
                ConsolidatedHolding.model_validate(data)
                for data in old_holdings
            ]

            return {
                "status": "retained",
                "dataset": "consolidateddata",
                "rows_read": 0,
                "rows_filtered": 0,
                "holdings_validated": 0,
                "holdings_retained": len(holdings),
                "output_file": str(output_path),
                "holdings": holdings,
                "message": str(exc),
            }

        return {
            "status": "failed",
            "dataset": "consolidateddata",
            "rows_read": 0,
            "rows_filtered": 0,
            "holdings_validated": 0,
            "output_file": str(output_path),
            "holdings": [],
            "message": str(exc),
        }