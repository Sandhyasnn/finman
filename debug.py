from fastapi import APIRouter, Request, HTTPException, Query, Path
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from logger import logger
import json
from typing import Optional

router = APIRouter(prefix="/debug", tags=["Debug"],)
templates = Jinja2Templates(directory="templates")

@router.get("/", response_class=HTMLResponse)
def debug_home(request: Request):
    return templates.TemplateResponse(
        request,
        "routelist.html"
    )

# Just dump the config as JSON. This is useful for debugging the config loading process.
@router.get("/config")
def debug_config(request: Request):
    return request.app.state.config.model_dump(by_alias=True)

# Dump the raw Excel rows as JSON. The entire sheet is dumped as a list of dictionaries, where each 
# dictionary represents a row in the sheet. This is useful for debugging the Excel reading process.
from ingestion.excel_reader import read_excel_rows
from fastapi.responses import JSONResponse
@router.get("/excel/{dataset}")
def debug_excel(request: Request, dataset: str):
    config = request.app.state.config
    # Dynamically fetch the dataset configuration based on the string argument
    ds_config = getattr(config.datasets, dataset)

    print(f"Reading Excel file: {ds_config.excel_file}, sheet: {ds_config.transactions.excel_sheet}, header row: {ds_config.transactions.header_row}")

    try:
        rows = read_excel_rows(
            excel_file=ds_config.excel_file,
            sheet_name=ds_config.transactions.excel_sheet,
            header_row=ds_config.transactions.header_row,
        )
    except ValueError as exc:
        logger.error(str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return rows

#-----------------------------------------------------------------
# Transaction Ingestion Debug routes
#-----------------------------------------------------------------

# Dump the mapped transactions as JSON. As this stage we have only mapped the columns from the Excel 
# sheet to the normalized transaction structure, but have not yet validated the data.
from ingestion.mapper import map_transaction_row, validate_mapping
@router.get("/transaction/{dataset}")
def debug_transactions(request: Request, dataset: str):
    config = request.app.state.config
    # Dynamically fetch the dataset configuration based on the string argument
    ds_config = getattr(config.datasets, dataset)

    try:
        rows = read_excel_rows(
            excel_file=ds_config.excel_file,
            sheet_name=ds_config.transactions.excel_sheet,
            header_row=ds_config.transactions.header_row,
        )
    except ValueError as exc:
        logger.error(str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not rows:
        raise HTTPException(status_code=400, detail=f"No data rows found in Excel sheet '{ds_config.transactions.excel_sheet}'.",)

    headers = list(rows[0].keys())

    errors = validate_mapping(ds_config.transactions, headers,)
    if errors:
        # 1. Output error explicitly to the terminal/console
        logger.error(f"Mapping validation failed for '{dataset}':\n" + "\n".join(f"  - {error}" for error in errors))

        # 2. Return formatted, line-separated text to the browser
        raise HTTPException(
            status_code=400, 
            detail = {
            "message": f"Transaction mapping validation failed for '{dataset}'",
            "errors": errors }
        )

    # 1. Initialize an empty list
    mapped_rows = []
    for row in rows:
        mapped_row = map_transaction_row(row, ds_config.transactions)
        mapped_rows.append(mapped_row)

    return mapped_rows

# Dump the mapped transactions as JSON. Here we have only mapped the columns from the Excel sheet to the 
# normalized transaction structure, and have also filtered using row filter and  
# validated the data using the Transaction model.
from ingestion.mapper import apply_row_filter
from ingestion.models import Transaction
@router.get("/transaction2/{dataset}")
def debug_transactions2(request: Request, dataset: str):
    config = request.app.state.config
    # Dynamically fetch the dataset configuration based on the string argument
    ds_config = getattr(config.datasets, dataset)

    try:
        rows = read_excel_rows(
            excel_file=ds_config.excel_file,
            sheet_name=ds_config.transactions.excel_sheet,
            header_row=ds_config.transactions.header_row,
        )
    except ValueError as exc:
        logger.error(str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not rows:
        raise HTTPException(status_code=400, detail=f"No data rows found in Excel sheet '{ds_config.transactions.excel_sheet}'.",)
    
    transactions = []

    for row_number, row in enumerate(rows, start=1):
        try:
            values = map_transaction_row(row, ds_config.transactions)
            # if row_number == 134:
            #     logger.debug(
            #         "DEBUG row 134: security=%r, transaction_date=%s, transaction_action=%s, asset_type=%r, quantity=%r, price=%r",
            #         values.get("security"),
            #         values.get("transaction_date"),
            #         values.get("transaction_action"),
            #         values.get("asset_type"),
            #         values.get("transaction_quantity"),
            #         values.get("transaction_price"),
            #     )
            if not apply_row_filter(values, ds_config.row_filter):
                continue
            transaction = Transaction.model_validate(values)
            transactions.append(transaction)
        except ValueError as exc:
            message = (f"Transaction validation failed for dataset " f"'{dataset}', row {row_number}: {exc}")
            logger.error(message)
            raise HTTPException(status_code=400, detail=message,) from exc

    return [
        transaction.model_dump(mode="json")
        for transaction in transactions
    ]

from ingestion.transactions import ingest_transactions
@router.get("/ingest")
def debug_ingest_transactions(request: Request):
    config = request.app.state.config

    output_file = (
        Path(config.server.cache_dir)
        / config.cache.transactions_file
    )

    try:
        result = ingest_transactions(datasets=config.datasets, output_file=output_file,)
    except ValueError as exc:
        logger.debug(str(exc))
        return {"status": "error", "message": str(exc),}    

    return {
        "status": "ok",
        "datasets": [
            {
                "dataset": dataset_result["dataset"],
                "rows_read": dataset_result["rows_read"],
                "rows_filtered": dataset_result["rows_filtered"],
                "transactions_validated": dataset_result["transactions_validated"],
            }
            for dataset_result in result["datasets"]
        ],
        "total_transactions": result["total_transactions"],
        "output_file": result["output_file"],
    }


@router.get("/tranjson")
@router.get("/tranjson/{dataset}")
def debug_transaction_json(request: Request, dataset: Optional[str] = None,
    rows: str = Query(default="4", description="Number of rows to return (e.g. 3, 4, 5) or ALL") ):
    cache_dir: Path = request.app.state.cache_dir

    # Decide which file to load
    if dataset is None:
        transactions_file = cache_dir / "transactions.json"
        label = "transactions"
    else:
        transactions_file = cache_dir / f"transactions_{dataset}.json"
        label = dataset

    if not transactions_file.exists():
        return {"error": f"Cache file not found: {transactions_file.name}"}

    with transactions_file.open("r", encoding="utf-8") as f:
        transactions = json.load(f)

    # Handle the rows parameter
    if rows.upper() == "ALL":
        data = transactions
    else:
        try:
            n = int(rows)
            if n < 0:
                return {"error": "rows must be a non-negative integer or ALL"}
            data = transactions[:n]
        except ValueError:
            return {
                "error": "Invalid 'rows' value. Use a number (3, 4, 5…) or ALL"
            }

    return {label: data}

#-----------------------------------------------------------------
# Prices Snapshot Ingestion Debug routes
#-----------------------------------------------------------------
from fastapi import Path as PathParam
@router.get("/trancol/{field}")
def get_unique_field_values(request: Request,
    field: str = PathParam(..., description="Field name to extract unique values from (e.g. asset_type)") ):
    transactions = request.app.state.transactions

    # If the wrog filed name is put explicitly call it out
    if not transactions or field not in transactions[0].model_fields:
        return {"error": f"Unknown transaction field: {field}"}

    # Collect unique non-null values for the given field
    unique_values = sorted({
        getattr(t, field)
        for t in transactions
        if getattr(t, field, None) is not None
    })

    return {
        "field": field,
        "count": len(unique_values),
        "unique_values": unique_values,
    }


from market.holdings import get_price_securities
@router.get("/price-securities")
def debug_price_securities(request: Request):
    transactions = request.app.state.transactions
    securities = get_price_securities(transactions)
    return {
        "Yahoo count": len(securities["yahoo"]),
        "AMFI count": len(securities["amfi"]),
        "prices": securities
    }

@router.get("/pricesjson")
def debug_prices_json(request: Request):
    cache_dir: Path = request.app.state.cache_dir
    price_snapshot_file = cache_dir / "price_snapshot.json"
    label = "Prices"

    if not price_snapshot_file.exists():
        return {"error": f"Cache file not found: {price_snapshot_file.name}. Run the refresh_prices route"}

    with price_snapshot_file.open("r", encoding="utf-8") as f:
        price_snapshot = json.load(f)

    return {label: price_snapshot}


# -----------------------------------------------------------------
# Tax Ingestion Debug routes
# -----------------------------------------------------------------
from ingestion.mapper import map_tax_row, validate_tax_mapping
from ingestion.models import TaxTransaction
@router.get("/tax/{dataset}")
def debug_tax(request: Request, dataset: str):
    config = request.app.state.config

    # Dynamically fetch the dataset configuration
    ds_config = getattr(config.datasets, dataset)

    try:
        rows = read_excel_rows(
            excel_file=ds_config.excel_file,
            sheet_name=ds_config.tax.excel_sheet,
            header_row=ds_config.tax.header_row,
        )
    except ValueError as exc:
        logger.error(str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not rows:
        raise HTTPException(status_code=400, detail=(f"No data rows found in Excel sheet " f"'{ds_config.tax.excel_sheet}'."))

    headers = list(rows[0].keys())

    errors = validate_tax_mapping(ds_config.tax, headers)

    if errors:
        logger.error(f"Tax mapping validation failed for '{dataset}':\n" + "\n".join(f"  - {error}" for error in errors))

        raise HTTPException(status_code=400, detail={
                "message": (f"Tax mapping validation failed for '{dataset}'"),
                "errors": errors,
            },
        )

    mapped_rows = []

    for row in rows:
        mapped_row = map_tax_row(row, ds_config.tax)
        mapped_rows.append(mapped_row)

    return mapped_rows

@router.get("/tax2/{dataset}")
def debug_tax2(request: Request, dataset: str):
    config = request.app.state.config

    ds_config = getattr(config.datasets, dataset)

    try:
        rows = read_excel_rows(
            excel_file=ds_config.excel_file,
            sheet_name=ds_config.tax.excel_sheet,
            header_row=ds_config.tax.header_row,
        )
    except ValueError as exc:
        logger.error(str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not rows:
        raise HTTPException(status_code=400,detail=(f"No data rows found in Excel sheet " f"'{ds_config.tax.excel_sheet}'.") )

    taxes = []

    for row_number, row in enumerate(rows, start=1):
        try:
            values = map_tax_row(row, ds_config.tax)

            tax = TaxTransaction.model_validate(values)
            taxes.append(tax)

        except ValueError as exc:
            message = (f"Tax validation failed for dataset " f"'{dataset}', row {row_number}: {exc}")

            logger.error(message)

            raise HTTPException(status_code=400, detail=message) from exc

    return [
        tax.model_dump(mode="json")
        for tax in taxes
    ]

from ingestion.tax_ingestion import ingest_tax
@router.get("/ingest-tax")
def debug_ingest_tax(request: Request):
    config = request.app.state.config

    output_file = (Path(config.server.cache_dir) / config.cache.tax_file)

    try:
        result = ingest_tax(datasets=config.datasets, output_file=output_file)

    except ValueError as exc:
        logger.debug(str(exc))
        return {"status": "error", "message": str(exc)}

    return {
        "status": result["status"],
        "datasets": [
            {
                "dataset": dataset_result["dataset"],
                "status": dataset_result["status"],
                "rows_read": dataset_result["rows_read"],
                "tax_validated": dataset_result["tax_validated"],
                "tax_retained": dataset_result.get(
                    "tax_retained",
                    0,
                ),
            }
            for dataset_result in result["datasets"]
        ],
        "total_tax_records": result["total_tax_records"],
        "output_file": result["output_file"],
    }

@router.get("/taxjson")
@router.get("/taxjson/{dataset}")
def debug_tax_json(
    request: Request, dataset: Optional[str] = None,
    rows: str = Query(default="4", description=("Number of rows to return " "(e.g. 3, 4, 5) or ALL")),):
    cache_dir: Path = request.app.state.cache_dir

    if dataset is None:
        tax_file = cache_dir / "tax.json"
        label = "tax"
    else:
        tax_file = cache_dir / f"tax_{dataset}.json"
        label = dataset

    if not tax_file.exists():
        return {"error": (f"Cache file not found: " f"{tax_file.name}")}

    with tax_file.open("r", encoding="utf-8") as f:
        tax = json.load(f)

    if rows.upper() == "ALL":
        data = tax
    else:
        try:
            n = int(rows)

            if n < 0:
                return {"error": ("rows must be a non-negative " "integer or ALL")}

            data = tax[:n]

        except ValueError:
            return {"error": ("Invalid 'rows' value. " "Use a number (3, 4, 5…) or ALL")}

    return {label: data}


# -----------------------------------------------------------------
# Holdings Debug routes
# -----------------------------------------------------------------
@router.get("/tranjson-by-closed/")
def debug_transactions_by_closed(request: Request,
    rows: str = Query(default="4", description="Number of rows to return for each group (e.g. 3, 4, 5) or ALL")):
    """
    Return up to N rows where position_closed == "CLOSED"
    and up to N rows where position_closed != "CLOSED".
    """
    cache_dir: Path = request.app.state.cache_dir
    transactions_file = cache_dir / "transactions.json"

    if not transactions_file.exists():
        return {"error": f"Cache file not found: {transactions_file.name}"}

    with transactions_file.open("r", encoding="utf-8") as f:
        transactions = json.load(f)

    # Split into the two groups
    closed = [t for t in transactions if t.get("position_closed") == "CLOSED"]
    not_closed = [t for t in transactions if t.get("position_closed") != "CLOSED"]

    # Handle the rows parameter
    if rows.upper() == "ALL":
        closed_data = closed
        not_closed_data = not_closed
    else:
        try:
            n = int(rows)
            if n < 0:
                return {"error": "rows must be a non-negative integer or ALL"}
            closed_data = closed[:n]
            not_closed_data = not_closed[:n]
        except ValueError:
            return {
                "error": "Invalid 'rows' value. Use a number (3, 4, 5…) or ALL"
            }

    return {
        "position_closed_NOT_CLOSED": {
            "count": len(not_closed),
            "showing": len(not_closed_data),
            "rows": not_closed_data,
        },
        "position_closed_CLOSED": {
            "count": len(closed),
            "showing": len(closed_data),
            "rows": closed_data,
        },
    }


from portfolio import build_current_holdings
@router.get("/holdings/{owner}/{asset_type_value}/{broker}")
def debug_holdings(request: Request,
    owner: str = Path(..., description="owner type value to filter (e.g. MALT, RSN)"),
    asset_type_value: str = Path(..., description="Asset type value to filter (e.g. Stock, MF(Equity))"),
    broker: str = Path(..., description="broker type value to filter (e.g. CBSL, ZERODHA") ):

    tx_data = request.app.state.transactions
    transactions = tx_data.get("transactions", tx_data) if isinstance(tx_data, dict) else tx_data

    if not transactions:
        raise HTTPException(status_code=404, detail="No transactions data found")

    # 1. Collect unique values using attribute access (t.owner)
    valid_owners = {t.owner for t in transactions if getattr(t, "owner", None) is not None}
    # 2. Case-insensitive lookup against the parameter string
    matched_type = next(
        (at for at in valid_owners if at.lower() == owner.lower()), 
        None
    )
    if not matched_type:
        raise HTTPException(status_code=400, detail=f"Unknown owner value: '{owner}'. Valid options: {list(valid_owners)}")

    # 3. Collect unique values using attribute access (t.asset_type)
    valid_asset_types = {t.asset_type for t in transactions if getattr(t, "asset_type", None) is not None}
    # 4. Case-insensitive lookup against the parameter string
    matched_type = next(
        (at for at in valid_asset_types if at.lower() == asset_type_value.lower()), 
        None
    )
    if not matched_type:
        raise HTTPException(status_code=400, detail=f"Unknown asset_type value: '{asset_type_value}'. Valid options: {list(valid_asset_types)}" )


    # 5. Collect unique values using attribute access (t.broker)
    valid_brokers = {t.broker for t in transactions if getattr(t, "broker", None) is not None}
    # 6. Case-insensitive lookup against the parameter string
    matched_type = next(
        (at for at in valid_brokers if at.lower() == broker.lower()), 
        None
    )
    if not matched_type:
        raise HTTPException(status_code=400, detail=f"Unknown broker value: '{broker}'. Valid options: {list(valid_brokers)}" )

    holdings = build_current_holdings(transactions, owner=owner, asset_type=asset_type_value, broker=broker)

    return {
        "asset_type": asset_type_value,
        "owner": owner,
        "broker": broker,
        "count": len(holdings),
        "holdings": [
            holding.model_dump(mode="json") if hasattr(holding, "model_dump") else holding
            for holding in holdings
        ],
    }

@router.get("/state/holdings")
def debug_state_holdings(request: Request):
    holdings = request.app.state.holdings

    return {
        "count": len(holdings),
        "holdings": [
            holding.model_dump(mode="json")
            for holding in holdings
        ],
    }

from portfolio import build_portfolio_summary
@router.get("/portfolio-summary/{owner}/{asset_type_value}/{broker}")
def debug_portfolio_summary(request: Request,
    owner: str = Path(..., description="owner type value to filter (e.g. MALT, RSN)"),
    asset_type_value: str = Path(..., description="Asset type value to filter (e.g. Stock, MF(Equity))"),
    broker: str = Path(..., description="borker value to filter (e.g. CBSL, ZERODHA") ):

    tx_data = request.app.state.transactions
    transactions = tx_data.get("transactions", tx_data) if isinstance(tx_data, dict) else tx_data

    if not transactions:
        raise HTTPException(status_code=404, detail="No transactions data found")

    # 1. Collect unique values using attribute access (t.owner)
    valid_owners = {t.owner for t in transactions if getattr(t, "owner", None) is not None}
    # 2. Case-insensitive lookup against the parameter string
    matched_type = next(
        (at for at in valid_owners if at.lower() == owner.lower()), 
        None
    )
    if not matched_type:
        raise HTTPException(status_code=400, detail=f"Unknown owner value: '{owner}'. Valid options: {list(valid_owners)}")

    # 3. Collect unique values using attribute access (t.asset_type)
    valid_asset_types = {t.asset_type for t in transactions if getattr(t, "asset_type", None) is not None}
    # 4. Case-insensitive lookup against the parameter string
    matched_type = next(
        (at for at in valid_asset_types if at.lower() == asset_type_value.lower()), 
        None
    )
    if not matched_type:
        raise HTTPException(status_code=400, detail=f"Unknown asset_type value: '{asset_type_value}'. Valid options: {list(valid_asset_types)}" )


    # 5. Collect unique values using attribute access (t.broker)
    valid_brokers = {t.broker for t in transactions if getattr(t, "broker", None) is not None}
    # 6. Case-insensitive lookup against the parameter string
    matched_type = next(
        (at for at in valid_brokers if at.lower() == broker.lower()), 
        None
    )
    if not matched_type:
        raise HTTPException(status_code=400, detail=f"Unknown broker value: '{broker}'. Valid options: {list(valid_brokers)}" )
                            
    summary = build_portfolio_summary(request.app.state.holdings, transactions, owner=owner, asset_type=asset_type_value, broker=broker)

    return {
        "asset_type": asset_type_value,
        "owner": owner,
        "broker": broker,
        "holdings": summary.model_dump(mode="json")
    }


from datetime import date
from portfolio import calculate_xirr
@router.get("/calcxirr")
def calc_xirr():
    # Define test inputs directly inside the route
    test_cash_flows = [-1000.0, 1100.0]
    test_dates = [date(2024, 1, 1), date(2024, 7, 1)]

    result = calculate_xirr(test_cash_flows, test_dates)

    return {
        "status": "success",
        "inputs": {
            "cash_flows": test_cash_flows,
            "dates": test_dates,
        },
        "output": {
            "xirr": result
        }
    }

from portfolio import build_holding_detail
@router.get("/holdings-all/{owner}/{asset_type_value}/{broker}")
def debug_holdings_all(request: Request, owner: str = Path(...), asset_type_value: str = Path(...),broker: str = Path(...)):
    transactions = request.app.state.transactions

    if not transactions:
        raise HTTPException(status_code=404, detail="No transactions data found")

    results = {}

    for scope in ("current", "total", "closed"):
        holdings = build_holdings(
            transactions,
            scope=scope,
            owner=owner,
            asset_type=asset_type_value,
            broker=broker,
        )

        results[scope] = [
            holding.model_dump(mode="json")
            for holding in holdings
        ]

    return {
        "asset_type": asset_type_value,
        "owner": owner,
        "broker": broker,
        "current": results["current"],
        "total": results["total"],
        "closed": results["closed"],
    }


from portfolio import build_current_holdings, build_holdings
@router.get("/holdings-detail/{owner}/{asset_type_value}/{broker}/{security}")
def debug_holdings_detail(request: Request, owner: str = Path(...), asset_type_value: str = Path(...),
                          broker: str = Path(...), security: str = Path(...)):
    transactions = request.app.state.transactions

    if not transactions:
        raise HTTPException(status_code=404, detail="No transactions data found")

    results = {}

    holdings = build_holding_detail(
        transactions,
        security=security,
        owner=owner,
        asset_type=asset_type_value,
        broker=broker,
    )

    if holdings is None:
        raise HTTPException(
            status_code=404,
            detail="Holding not found",
        )
    
    return {
        "security": security,
        "asset_type": asset_type_value,
        "owner": owner,
        "broker": broker,
        "asset_subtype": holdings.asset_subtype,
        "current": holdings.current,
        "total": holdings.total,
        "closed": holdings.closed,
        "transaction":holdings.transactions
    }

from portfolio import build_portfolio_view
@router.get("/portfolio/{owner}/{asset_type_value}/{broker}")
def debug_portfolio_view(request: Request, owner: str = Path(...),asset_type_value: str = Path(...),broker: str = Path(...)):
    view = build_portfolio_view(
        request.app.state.transactions,
        request.app.state.holdings,
        owner=owner,
        asset_type=asset_type_value,
        broker=broker,
    )

    return view.model_dump(mode="json")

@router.get("/portfolio-view/{owner}")
def debug_portfolio_view(request: Request, owner: str = Path(...),):
    selected_owner = None if owner.upper() == "ALL" else owner

    view = build_portfolio_view(request.app.state.transactions, request.app.state.holdings,owner=selected_owner)

    return view.model_dump(mode="json")

from portfolio import build_broker_view
@router.get("/broker-view/{owner}")
def debug_broker_view(request: Request, owner: str = Path(...)):
    selected_owner = None if owner.upper() == "ALL" else owner

    view = build_broker_view(request.app.state.transactions, request.app.state.holdings, owner=selected_owner)

    return view.model_dump(mode="json")

# -----------------------------------------------------------------
# Tax Debug routes
# -----------------------------------------------------------------
from tax import build_tax_portfolio_view, build_tax_broker_view, build_tax_summary
from ingestion.models import TaxView
@router.get("/taxview")
def debug_tax(request: Request, fy: str, owner: str = "ALL", view: str = "portfolio"):
    """
    Debug tax routes by returning the tax view as JSON.

    view:
        portfolio -> Tax Portfolio grouping
        broker    -> Tax Broker grouping
    """
    if owner == "ALL":
        selected_owners = None
    else:
        selected_owners = request.app.state.config.frontend.owners[owner]

    if view == "portfolio":
        if selected_owners is None:
            tax_view = build_tax_portfolio_view(request.app.state.tax, fy=fy, owner=None)
        else:
            sections = []

            for selected_owner in selected_owners:
                owner_view = build_tax_portfolio_view(request.app.state.tax, fy=fy, owner=selected_owner)
                sections.extend(owner_view.sections)

            tax_view = TaxView(fy=fy, owner=owner, sections=sections, 
                               summary=build_tax_summary(
                                [
                                    transaction
                                    for section in sections
                                    for transaction in section.transactions
                                ]
                            ) )

    elif view == "broker":
        if selected_owners is None:
            tax_view = build_tax_broker_view(request.app.state.tax, fy=fy, owner=None)
        else:
            sections = []

            for selected_owner in selected_owners:
                owner_view = build_tax_broker_view(request.app.state.tax, fy=fy, owner=selected_owner,)
                sections.extend(owner_view.sections)

            tax_view = TaxView(fy=fy, owner=owner, sections=sections, 
                               summary=build_tax_summary(
                                [
                                    transaction
                                    for section in sections
                                    for transaction in section.transactions
                                ]
                            ) )

    else:
        raise HTTPException(status_code=400, detail="view must be 'portfolio' or 'broker'")

    return tax_view.model_dump(mode="json")


# -----------------------------------------------------------------
# Consolidate Data Debug routes
# -----------------------------------------------------------------
@router.get("/consolidated")
def debug_consolidated(request: Request):
    cache_dir: Path = request.app.state.cache_dir
    with open(cache_dir / "consolidated.json", encoding="utf-8") as f:
        return json.load(f)

# -----------------------------------------------------------------
# Research Debug routes
# -----------------------------------------------------------------
from research.research import get_research, load_research_index
from research.download_nav_history import download_nav_history, NAV_CACHE_DIR
from datetime import datetime

@router.get("/research/nav/{amfi_code}")
def debug_research_nav(request: Request, amfi_code: str):
    nav = download_nav_history(amfi_code)
    return {
            "length": len(nav.history),
            "history": nav,
        }

@router.get("/research/nav-cache/{amfi_code}")
def debug_research_nav_cache(request: Request, amfi_code: str):
    cache_file = NAV_CACHE_DIR / f"{amfi_code}.json"

    if not cache_file.exists():
        return {"error": f"Cache file not found: {cache_file}"}

    with cache_file.open("r", encoding="utf-8") as f:
        data = json.load(f)

    return {
            "length": len(data["history"]),
            "data": data,
        }

@router.get("/research/{amfi_code}")
def debug_research_viewmodel(request: Request, amfi_code: str):
    """
    Return the exact view-model that research.html receives,
    as JSON. Useful for inspecting units (fraction vs percent)
    and the shape of rolling / calendar / risk data.
    """
    fund = get_research(amfi_code)

    research = load_research_index()
    generated = research.generated_at
    try:
        generated = datetime.fromisoformat(generated).strftime("%d %b %Y %H:%M")
    except Exception:
        pass

    # Import here to avoid circular import at module load time
    # (finman.py imports the debug router).
    from finman import build_research_viewmodel

    vm = build_research_viewmodel(fund, generated_at=generated)
    return vm

@router.get("/research/json/mf_master")
def debug_mf_master_json(request: Request):
    from finman import BASE_DIR
    cache_file = BASE_DIR / "static" / "mf_master.json"

    if not cache_file.exists():
        return {"error": f"Cache file not found: {cache_file}"}

    with cache_file.open("r", encoding="utf-8") as f:
        data = json.load(f)

    return {
            "length": len(data["funds"]),
            "data": data,
        }

