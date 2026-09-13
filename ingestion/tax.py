"""
Tax ingestion.

Reads configured Tax worksheets, maps rows to TaxTransaction records, validates them with Pydantic, 
maintains per-dataset caches, and writes the combined tax.json cache.

Dataset failures are isolated. When a dataset fails and a previous valid cache exists, the previous 
records are retained and the overall status is reported as partial.
"""
from pathlib import Path

from pydantic import BaseModel

from ingestion.config import DatasetConfig
from ingestion.excel_reader import read_excel_rows
from ingestion.mapper import map_tax_row, validate_tax_mapping
from ingestion.models import TaxTransaction
from ingestion.json_io import read_json, write_json

from logger import logger

class DatasetTaxIngestionResult(BaseModel):
    dataset: str
    rows_read: int
    tax_validated: int
    tax: list[TaxTransaction]


def _ingest_dataset_tax(dataset_name: str, dataset: DatasetConfig) -> DatasetTaxIngestionResult:
    """
    Ingest Tax records from one configured dataset.
    """
    logger.info("Starting tax ingestion for dataset '%s'", dataset_name)

    rows = read_excel_rows(
        excel_file=dataset.excel_file,
        sheet_name=dataset.tax.excel_sheet,
        header_row=dataset.tax.header_row,
    )

    if not rows:
        raise ValueError(f"Dataset '{dataset_name}' contains no tax rows")

    headers = list(rows[0].keys())

    errors = validate_tax_mapping(dataset.tax, headers)

    if errors:
        raise ValueError(f"Tax mapping validation failed for " f"dataset '{dataset_name}':\n"
            + "\n".join(
                f"- {error}"
                for error in errors
            )
        )

    tax_records = []

    for row_number, row in enumerate(rows, start=1):
        try:
            values = map_tax_row(row, dataset.tax)

            tax_record = TaxTransaction.model_validate(values)
            tax_records.append(tax_record)

        except ValueError as exc:
            message = (f"Tax validation failed for dataset " f"'{dataset_name}', row {row_number}: " f"{exc}")

            logger.debug(message)
            raise ValueError(message) from exc

    logger.info("Validated %d tax records from dataset '%s'", len(tax_records), dataset_name)

    return DatasetTaxIngestionResult(
        dataset=dataset_name,
        rows_read=len(rows),
        tax_validated=len(tax_records),
        tax=tax_records,
    )


def ingest_tax(datasets, output_file: str | Path) -> dict:
    """
    Ingest all configured Tax datasets and produce tax.json.

    Each dataset is cached independently. If a dataset fails,
    its previous successful cache is retained when available.
    """
    output_path = Path(output_file)
    cache_dir = output_path.parent

    dataset_results = []
    all_tax = []

    for dataset_name in datasets.model_fields:
        dataset = getattr(datasets, dataset_name)

        dataset_cache_file = (cache_dir / f"tax_{dataset_name}.json")

        try:
            result = _ingest_dataset_tax(dataset_name, dataset)

            dataset_tax = result.tax

            write_json(dataset_cache_file,
                [ record.model_dump(mode="json")
                 for record in dataset_tax] )

            all_tax.extend(dataset_tax)

            dataset_results.append(
                {
                    "dataset": dataset_name,
                    "status": "ok",
                    "rows_read": result.rows_read,
                    "tax_validated": result.tax_validated,
                    "tax_retained": 0,
                }
            )

        except ValueError as exc:
            logger.error("Tax ingestion failed for dataset '%s': %s", dataset_name, exc)

            if dataset_cache_file.exists():

                old_tax = read_json(dataset_cache_file, default=[])

                retained_tax = [
                    TaxTransaction.model_validate(record)
                    for record in old_tax
                ]

                all_tax.extend(retained_tax)

                dataset_results.append(
                    {
                        "dataset": dataset_name,
                        "status": "retained",
                        "rows_read": None,
                        "tax_validated": 0,
                        "tax_retained": len(retained_tax),
                        "message": str(exc),
                    }
                )

            else:
                dataset_results.append(
                    {
                        "dataset": dataset_name,
                        "status": "failed",
                        "rows_read": 0,
                        "tax_validated": 0,
                        "tax_retained": 0,
                        "message": str(exc),
                    }
                )

    write_json(output_path,
        [ record.model_dump(mode="json")
         for record in all_tax] )

    has_retained_dataset = any(
        result["status"] == "retained"
        for result in dataset_results
    )

    has_failed_dataset = any(
        result["status"] == "failed"
        for result in dataset_results
    )

    if has_failed_dataset:
        overall_status = "error"
    elif has_retained_dataset:
        overall_status = "partial"
    else:
        overall_status = "ok"

    logger.info("Wrote %d tax records to '%s'", len(all_tax), output_path)

    return {
        "status": overall_status,
        "datasets": dataset_results,
        "total_tax_records": len(all_tax),
        "output_file": str(output_path),
        "tax": all_tax,
    }
