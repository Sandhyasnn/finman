"""
Configuration-driven mapping of raw Excel rows to normalized fields.

The mapper interprets the MappingValue definitions from FinMan's
validated configuration. It does not know which dataset produced
the row.

Supported mapping forms are:

    ExcelFieldMapping  -> value from an Excel column
    TransformMapping   -> value derived from a source column
    string             -> literal value
    "derived"          -> intentionally unresolved value
    None               -> no value
"""
from datetime import date, datetime
from typing import Any

from ingestion.config import (
    ExcelFieldMapping,
    TransformMapping,
    TransactionMapping,
    TaxMapping,
    RowFilter
)
from ingestion.models import Transaction, TaxTransaction

# ============================================================
# Internal configuration functions
# ============================================================
def _convert_value(value: Any, target_type: str,) -> Any:
    """
    Convert an Excel value to the FinMan type specified by an ExcelFieldMapping.
    Internal helper: handles text, float, and date conversion.    
    """

    if value is None:
        return None

    if target_type == "text":
        return str(value)

    if target_type == "float":
        return float(value)

    if target_type == "date":
        if isinstance(value, datetime):
            return value.date()

        if isinstance(value, date):
            return value

        raise ValueError(
            f"Expected date value, got {type(value).__name__}: {value}"
        )

    raise ValueError(f"Unsupported mapping type: {target_type}")

def _apply_transform(value: Any, transform: dict[str, Any],) -> Any:
    """
    Apply a configured transformation to a source value.
    Internal helper: currently supports configured contains and identity transformations.    
    """
    if "contains" in transform:
        search_value = transform["contains"]

        if value is None:
            return transform["false"]

        return (
            transform["true"]
            if search_value in str(value)
            else transform["false"]
        )

    if transform.get("type") == "identity":
        return value

    raise ValueError(f"Unsupported transformation: {transform}")

# ============================================================
# Internal configuration functions
# ============================================================
def map_value(mapping: Any, row: dict[str, Any],) -> Any:
    """
    Resolve one configured mapping against a raw Excel row.
    The mapping definition, rather than the mapper code, determines what the resulting value means.
    Supports direct Excel fields, transformations, literal values, and unresolved "derived" values.
    Returns: The normalized field value.
    Supported mapping types:
        ExcelFieldMapping  -> value from Excel row
        TransformMapping   -> transformed source value
        str                -> literal value, except "derived"
        None               -> None
        "derived"           -> None for now
    """
    if mapping is None:
        return None

    if isinstance(mapping, ExcelFieldMapping):
        # A missing configured header is a configuration/data contract
        # error. Do not silently convert it to None.
        if mapping.header not in row:
            raise ValueError(f"Excel column '{mapping.header}' not found")
        return _convert_value(row[mapping.header],mapping.type,)

    if isinstance(mapping, TransformMapping):
        source_value = row.get(mapping.source)
        return _apply_transform(source_value, mapping.transform,)

    if isinstance(mapping, str):
        # "derived" fields belong to a later processing stage.
        # The ingestion phase deliberately leaves them unresolved.
        if mapping == "derived":
            return None

        return mapping

    raise TypeError(f"Unsupported mapping type: {type(mapping).__name__}")


# ============================================================
# Transaction Validation and  Mapping functions
# ============================================================
def validate_mapping(mapping: TransactionMapping, headers: list[str],) -> list[str]:
    """
    Validate that all Excel columns referenced by a transaction mapping exist in the worksheet headers.
    Performs this check before row processing so mapping/configuration errors are reported early.
    Returns a list of mapping errors. An empty list means the mapping is compatible with the Excel headers.
    """    
    errors = []

    for field_name in TransactionMapping.model_fields:
        field_mapping = getattr(mapping, field_name)

        if isinstance(field_mapping, ExcelFieldMapping):
            if field_mapping.header not in headers:
                errors.append(
                    f"{field_name}: Excel column "
                    f"'{field_mapping.header}' not found"
                )
        elif isinstance(field_mapping, TransformMapping):
            if field_mapping.source not in headers:
                errors.append(
                    f"{field_name}: Excel column "
                    f"'{field_mapping.source}' not found"
                )        

    return errors

def map_transaction_row(row: dict[str, Any], mapping: TransactionMapping,) -> dict[str, Any]:
    """
    Map one raw Excel row to the complete normalized FinMan transaction field set.
    Performs configuration-driven mapping only; Pydantic validation is performed separately.
    Returns: Dictionary containing normalized transaction fields.
    """    
    return {
        "security": map_value(mapping.security, row),
        "transaction_date": map_value(mapping.transaction_date, row),
        "transaction_action": map_value(mapping.transaction_action, row),
        "transaction_quantity": map_value(mapping.transaction_quantity, row),
        "transaction_price": map_value(mapping.transaction_price, row),
        "transaction_value": map_value(mapping.transaction_value, row),
        "position_closed": map_value(mapping.position_closed, row),
        "owner": map_value(mapping.owner, row),
        "asset_type": map_value(mapping.asset_type, row),
        "asset_subtype": map_value(mapping.asset_subtype, row),
        "broker": map_value(mapping.broker, row),
        "buy_date": map_value(mapping.buy_date, row),
        "buy_price": map_value(mapping.buy_price, row),
        "current_price": map_value(mapping.current_price, row),
        "current_value": map_value(mapping.current_value, row),
        "exchange_rate": map_value(mapping.exchange_rate, row),
        "transaction_price_usd": map_value(mapping.transaction_price_usd, row),
        "transaction_value_usd": map_value(mapping.transaction_value_usd, row),
    }

def map_transaction(row: dict[str, Any], mapping: TransactionMapping,) -> Transaction:
    """
    Map one raw Excel row and validate the resulting values as a Transaction.
    Returns: Validated Transaction.    
    """
    values = map_transaction_row(row, mapping)
    try:
        return Transaction.model_validate(values)
    except Exception as exc:
        errors = []

        for error in exc.errors():
            field_name = error["loc"][0]
            field_mapping = getattr(mapping, field_name)

            if isinstance(field_mapping, ExcelFieldMapping):
                excel_header = field_mapping.header
                field_value = row.get(excel_header)

                errors.append(
                    f"field '{field_name}', "
                    f"Excel column '{excel_header}', "
                    f"value={field_value!r}: "
                    f"{error['msg']}"
                )
            else:
                errors.append(f"field '{field_name}': " f"{error['msg']}")

        raise ValueError("; ".join(errors)) from exc

def apply_row_filter(row: dict[str, Any], row_filter: RowFilter | None,) -> bool:
    """
    Determine whether a mapped row should be included based on the configured row-filter conditions.
    All configured conditions must pass for the row to be included.
    Returns: True when the row should be retained; otherwise False.
    """
    if row_filter is None:
        return True

    for condition in row_filter.conditions:
        field_value = row.get(condition.field)

        if condition.op == "not_null":
            if field_value is None:
                return False

        elif condition.op == "null":
            if field_value is not None:
                return False

        elif condition.op == "==":
            if field_value != condition.value:
                return False

        elif condition.op == "!=":
            if field_value == condition.value:
                return False

        else:
            raise ValueError(
                f"Unsupported row filter operator '{condition.op}' "
                f"for field '{condition.field}'"
            )
    return True


# ============================================================
# Tax Transaction Validation and  Mapping functions
# ============================================================
def validate_tax_mapping(mapping: TaxMapping, headers: list[str],) -> list[str]:
    """
    Validate that all Excel columns referenced by a tax mapping
    exist in the worksheet headers.
    """
    errors = []

    for field_name in TaxMapping.model_fields:
        if field_name in ("excel_sheet", "header_row"):
            continue

        field_mapping = getattr(mapping, field_name)

        if isinstance(field_mapping, ExcelFieldMapping):
            if field_mapping.header not in headers:
                errors.append(
                    f"{field_name}: Excel column "
                    f"'{field_mapping.header}' not found"
                )
        elif isinstance(field_mapping, TransformMapping):
            if field_mapping.source not in headers:
                errors.append(
                    f"{field_name}: Excel column "
                    f"'{field_mapping.source}' not found"
                )

    return errors

def map_tax_row(row: dict[str, Any], mapping: TaxMapping) -> dict[str, Any]:
    """
    Map one Excel Tax row to the normalized TaxTransaction field set.
    """
    return {
        "fy": map_value(mapping.fy, row),
        "security": map_value(mapping.security, row),
        "owner": map_value(mapping.owner, row),
        "asset_type": map_value(mapping.asset_type, row),
        "broker": map_value(mapping.broker, row),        
        "sale_date": map_value(mapping.sale_date, row),
        "buy_price": map_value(mapping.buy_price, row),
        "quantity": map_value(mapping.quantity, row),
        "first_purchase_date": map_value(mapping.first_purchase_date, row),
        "holding_period": map_value(mapping.holding_period, row),
        "gain_type": map_value(mapping.gain_type, row),
        "buy_value": map_value(mapping.buy_value, row),
        "sell_value": map_value(mapping.sell_value, row),
        "capital_gain": map_value(mapping.capital_gain, row),
        "tax_rate": map_value(mapping.tax_rate, row),
        "tax": map_value(mapping.tax, row),
        "exchange_rate": map_value(mapping.exchange_rate, row),
        "buy_value_usd": map_value(mapping.buy_value_usd, row),
        "sell_value_usd": map_value(mapping.sell_value_usd, row),
        "capital_gain_usd": map_value(mapping.capital_gain_usd, row),
    }

def map_tax(row: dict[str, Any], mapping: TaxMapping) -> TaxTransaction:
    """
    Map one raw Excel row and validate it as a TaxTransaction.
    """
    values = map_tax_row(row, mapping)

    try:
        return TaxTransaction.model_validate(values)

    except Exception as exc:
        errors = []

        for error in exc.errors():
            field_name = error["loc"][0]
            field_mapping = getattr(mapping, field_name)

            if isinstance(field_mapping, ExcelFieldMapping):
                excel_header = field_mapping.header
                field_value = row.get(excel_header)

                errors.append(
                    f"field '{field_name}', "
                    f"Excel column '{excel_header}', "
                    f"value={field_value!r}: "
                    f"{error['msg']}"
                )
            else:
                errors.append(f"field '{field_name}': {error['msg']}")

        raise ValueError("; ".join(errors)) from exc
    