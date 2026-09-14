"""
FinMan transaction ingestion.

Reads the worksheet and header row defined by each dataset's TransactionMapping, maps and validates 
transaction rows, maintains per-dataset caches, and writes the combined transactions.json cache.

This module deliberately stops at normalized transactions.
"""
from pydantic import BaseModel
from pathlib import Path

from ingestion.config import DatasetConfig
from logger import logger
from ingestion.models import Transaction
from ingestion.config import TransactionMapping

from ingestion.excel_reader import read_excel_rows
from ingestion.mapper import map_transaction_row, validate_mapping, apply_row_filter, map_filter_values
from ingestion.json_io import read_json, write_json

# Normalized FinMan investment transaction.
# Represents the common transaction structure produced by ingestion from all supported Excel datasets.
# Excel-specific field names and mappings do not belong in this model.
class DatasetIngestionResult(BaseModel):
    dataset: str
    rows_read: int
    rows_filtered: int
    transactions_validated: int
    transactions: list[Transaction]

def _ingest_dataset_transactions(dataset_name: str, dataset: DatasetConfig,) -> dict:
    """
    Ingest transactions from one configured dataset.

    Reads the configured Excel worksheet, validates its mapping, applies the configured row filter before full
    transaction mapping, validates retained rows with Pydantic, and returns the successful dataset result.

    Returns:
        Dataset ingestion result containing the validated transactions.

    Raises:
        ValueError: If the mapping or any retained transaction row is invalid.
    """
    logger.info("Starting transaction ingestion for dataset '%s'", dataset_name,)

    rows = read_excel_rows(
        excel_file=dataset.excel_file,
        sheet_name=dataset.transactions.excel_sheet,
        header_row=dataset.transactions.header_row,
    )

    if not rows:
        raise ValueError(f"Dataset '{dataset_name}' contains no transaction rows")

    headers = list(rows[0].keys())

    errors = validate_mapping(dataset.transactions, headers,)
    if errors:
        raise ValueError(
            f"Transaction mapping validation failed for "
            f"dataset '{dataset_name}':\n"
            + "\n".join(f"- {error}" for error in errors)
        )

    transactions = []
    rows_filtered = 0

    for row_number, row in enumerate(rows, start=1):
        try:
            # Map only the fields needed to evaluate the row filter.
            # This allows unwanted rows to be discarded before converting the remaining transaction fields.
            filter_values = map_filter_values(row, dataset.transactions, dataset.row_filter)            

            # If the row filter succeeds on the row, move to the next while keep a count of the rows that have been filtered
            if not apply_row_filter(filter_values, dataset.row_filter):
                rows_filtered += 1
                continue

            # Fully map only those rows that passed the configured row filter.
            # This avoids mapping colulmns in filterd rows that may not have appropriate values (like invalid dates)
            values = map_transaction_row(row, dataset.transactions)

            transaction = Transaction.model_validate(values)
            transactions.append(transaction)

        except ValueError as exc:
            message = f"Transaction validation failed for dataset " f"'{dataset_name}', row {row_number}: {exc}"
            logger.debug(message)
            raise ValueError(message) from exc

    logger.info("Validated %d transactions from dataset '%s'", len(transactions), dataset_name,)

    return DatasetIngestionResult(
        dataset=dataset_name,
        rows_read=len(rows),
        rows_filtered=rows_filtered,
        transactions_validated=len(transactions),
        transactions=transactions,
    )

def ingest_transactions(datasets, output_file: str | Path) -> DatasetIngestionResult:
    """
    Ingest all configured transaction datasets and produce the combined transaction cache.
    Each successful dataset is cached independently. When a dataset fails, its previous successful 
    cache is retained when available so other datasets can continue processing.
    Inputs:
        datasets: The datasets config in config.json
        output_file: The json file the output should be written to
    Returns: Overall ingestion status, per-dataset results, transaction count, and output file information.
    """
    output_path = Path(output_file)
    cache_dir = output_path.parent

    dataset_results = []
    all_transactions = []

    # Go though all the datasets
    for dataset_name in datasets.model_fields:
        dataset = getattr(datasets, dataset_name)  # Gets the specific dataset config ot type DatasetConfig

        dataset_cache_file = (cache_dir / f"transactions_{dataset_name}.json")

        try:
            # Get the actual data in the DatasetConfig format
            result = _ingest_dataset_transactions(dataset_name, dataset)
            # Extract the transaction part from the result
            dataset_transactions = result.transactions
            # Write the transactions set to a json file defined before the try statemeent
            write_json(dataset_cache_file,
                [ transaction.model_dump(mode="json")
                 for transaction in dataset_transactions ] )

            # Simulatamneouly append the transactions to the all_transactions / overall transactions
            for transaction in dataset_transactions:
                all_transactions.append(transaction)

            # Capture the sucessful read results summary for detailed status later
            dataset_results.append(
                {
                    "dataset": dataset_name,
                    "status": "ok",
                    "rows_read": result.rows_read,
                    "rows_filtered": result.rows_filtered,
                    "transactions_validated": result.transactions_validated,
                    "transactions_retained": 0,
                }
            )

        except ValueError as exc:
            logger.error("Transaction ingestion failed for dataset '%s': %s", dataset_name, exc)

            if dataset_cache_file.exists():
                # Since the new load failed try to get it from the existing json file for this dataset
                old_transactions = read_json(dataset_cache_file, default=[])

                # Again append the transactions to the all_transactions / overall transactions
                for transaction_data in old_transactions:
                    transaction = Transaction.model_validate(transaction_data)
                    all_transactions.append(transaction)

                # Capture the sucessful retained results summary for detailed status later
                dataset_results.append(
                    {
                        "dataset": dataset_name,
                        "status": "retained",
                        "rows_read": None,
                        "rows_filtered": None,
                        "transactions_validated": 0,
                        "transactions_retained": len(old_transactions),
                        "message": str(exc),
                    }
                )

            else:
                # If the dataset was never uploaded (or successfully ever) then the json does not exist
                # In this case just update the failed status accordingly
                dataset_results.append(
                    {
                        "dataset": dataset_name,
                        "status": "failed",
                        "rows_read": 0,
                        "rows_filtered": 0,
                        "transactions_validated": 0,
                        "message": str(exc),
                    }
                )

    # Write the all_transactions / overall transactions to the json file
    write_json(output_path,
        [ transaction.model_dump(mode="json")
         for transaction in all_transactions] )

    logger.info("Wrote %d transactions to '%s'", len(all_transactions), output_path)

    has_retained_dataset = False
    has_failed_dataset = False

    # Go through the result set and create a retained / failed status column info
    for dataset_result in dataset_results:
        if dataset_result["status"] == "retained":
            has_retained_dataset = True

        if dataset_result["status"] == "failed":
            has_failed_dataset = True

    if has_failed_dataset:   # If any one dataset failed the overall status is failed
        overall_status = "error"
    elif has_retained_dataset:   # If any one dataset was retained the overall status is partial
        overall_status = "partial"
    else:
        overall_status = "ok"

    return {
        "status": overall_status,
        "datasets": dataset_results,
        "total_transactions": len(all_transactions),
        "output_file": str(output_path),
        "transactions": all_transactions
    }