"""
FinMan application and server entry point.
Application startup and shutdown lifecycle
1. Load and validate configuration.
2. Register development-only debug routes when debug mode is enabled.
3. Configure application logging.
4. Initialize the cache directory.
5. Ingest transactions.
6. Ingest tax records.
7. Refresh market prices.
8. Build current holdings.
9. Yield control to the running application.

Development-only Uvicorn options such as --reload are supplied
through the development BAT file and are not part of the application
configuration.
"""
import argparse, json, os
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime
from urllib.parse import parse_qs, quote

import uvicorn
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from logger import configure_logger, logger
from ingestion.config import load_config, FinManConfig, get_config_environment
from ingestion.transactions import ingest_transactions
from market.prices import refresh_prices
from market.external_links import configure_external_links
from ingestion.tax import ingest_tax
from ingestion.json_io import read_json, write_json
from portfolio import (
    build_holdings,
    build_current_holdings,
    build_holding_detail,
    build_portfolio_view,
    build_broker_view,
    build_portfolio_summary
)
from tax import (
    build_tax_portfolio_view,
    build_tax_broker_view,
    build_tax_detail,
    build_tax_summary
)
from ingestion.models import PortfolioView, BrokerView, TaxView
from ai.service import explain_holding, check_ai_health
from debug import router as debug_router

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
# ------------------------------------------------------------
# Application Functions
# ------------------------------------------------------------
def run_ingestion(app):
    # Application-level orchestration around ingestion services.
    config = app.state.config
    output_file = Path(app.state.cache_dir) / config.cache.transactions_file

    try:
        result = ingest_transactions(datasets=config.datasets, output_file=output_file)
        app.state.transactions = result["transactions"]   # Update the global app state with the transactions list
        app.state.ingestion_status = {      # Update the global app state with the ingestion status
            "status": result["status"],
            "datasets": result["datasets"],
            "total_transactions": result["total_transactions"],
            "output_file": result["output_file"],
        }
        return result  # Returns the status and transactions
    except ValueError as exc:
        app.state.ingestion_status = {"status": "error", "message": str(exc)}
        raise

def run_tax_ingestion(app): 
    # Application-level orchestration around tax ingestion services.
    config = app.state.config
    output_file = (Path(app.state.cache_dir) / config.cache.tax_file)

    try:
        result = ingest_tax(datasets=config.datasets, output_file=output_file)

        app.state.tax = result["tax"]
        app.state.tax_ingestion_status = {
            "status": result["status"],
            "datasets": result["datasets"],
            "total_tax_records": result["total_tax_records"],
            "output_file": result["output_file"],
        }

        return result

    except ValueError as exc:
        logger.error("Tax ingestion failed: %s", exc)
        app.state.tax = []
        app.state.tax_ingestion_status = {"status": "error","message": str(exc)}
        return app.state.tax_ingestion_status

def run_price_refresh(app):
    # Application-level orchestration around price services.
    config = app.state.config
    output_file = Path(app.state.cache_dir) / config.cache.price_snapshot
    transactions_file = Path(app.state.cache_dir) / config.cache.transactions_file

    result = refresh_prices(transactions=app.state.transactions, output_file=output_file)

    app.state.price_snapshot = result["prices"]  # Update the global app state with the prices snapshot
    app.state.price_refresh_status = result

    # Persist the price-enriched transactions.
    # refresh_prices() owns price_snapshot.json; application-level
    # orchestration owns persistence of the updated transactions.
    write_json(transactions_file,
        [ transaction.model_dump(mode="json")
         for transaction in app.state.transactions ] )
    
    return result  # Returns the status and transactions

def refresh_data(app):
    """Refresh all FinMan data without restarting the application."""
    run_ingestion(app)
    run_tax_ingestion(app)
    run_price_refresh(app)

    # you have to rebuild the holding since the transactions could have changed
    app.state.holdings = build_current_holdings(app.state.transactions)

def _resolve_owners(owner: str, owner_map: dict[str, list[str]]) -> list[str | None]:
    """
    Resolve the UI owner selection into underlying owners.

    Returns:
        [None]                 when ALL owners are requested
        [owner]                for a single underlying owner
        [owner1, owner2, ...]  for a grouped UI owner
    """
    if owner == "ALL":
        return [None]

    return owner_map[owner]

# ------------------------------------------------------------
# Application lifespan
# ------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application startup:
        Initialize FinMan at startup and perform shutdown work later if needed.
    1. Load and validate configuration.
    2. Register development-only debug routes when debug mode is enabled
    3. Configure application logging.
    4. Initialize the cache directory.
    5. Ingest transactions.
    6. Ingest tax records.
    7. Refresh market prices.
    8. build current holdings
    9. Yield control to the running application.       
    """
    # 1. Load and validate configuration.
    app.state.base_dir = BASE_DIR
    app.state.config = load_config(BASE_DIR)

    # Initialize external-link URL templates from the same validated
    # configuration used by the rest of FinMan.
    configure_external_links(app.state.config.model_dump())

    # 2. Register development-only debug routes when debug mode is enabled
    if app.state.config.server.debug:
        app.include_router(debug_router)

    # 3. Configure logging using the validated configuration.
    configure_logger(app.state.config.server.log_level, app.state.config.server.log_file)

    # 4. Initialize the cache directory.
    cache_dir = Path(app.state.config.server.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    app.state.cache_dir = cache_dir

    # 5. Run transaction ingestion automatically at startup.
    # 6. Run tax ingestion automatically at startup.
    # 7. Refresh current market prices.
    # 8. Build current holdings using the enriched transaction data.
    refresh_data(app)

    # 9. Application is ready to serve requests.
    yield

    # Shutdown work can be added here later (after yield).

# ------------------------------------------------------------
# Application initialization
# ------------------------------------------------------------
app = FastAPI(title="FinMan", version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

# ------------------------------------------------------------
# Jinja filters to be used by HTML for formatting purposes
# ------------------------------------------------------------
def format_number(value):
    """Display monetary/whole-number values with no decimals."""
    if value is None:
        return "—"
    return f"{value:,.0f}"

def format_decimal(value, decimal=2):
    """Display quantities/prices with two decimals."""
    if value is None:
        return "—"
    return f"{value:,.{decimal}f}"

def format_percent(value, decimal=2):
    """Display decimal percentage values as percentages."""
    if value is None:
        return "—"
    return f"{value * 100:.{decimal}f}%"

def format_currency(value, currency="INR", decimal=0):
    # Formats currency by default to Indian number system (lakhs, crores)
    # If nothing is done python tends to do it in western system by default - like for USD below
    if value is None:
        return "—"

    symbol = {"INR": "₹", "USD": "$"}.get(currency, f"{currency} ")
    negative = value < 0
    value = abs(value)

    number = f"{value:.{decimal}f}"
    int_part, _, dec_part = number.partition(".")

    if currency == "INR" and len(int_part) > 3:
        last3, rest = int_part[-3:], int_part[:-3]
        groups = []
        while rest:
            groups.insert(0, rest[-2:])
            rest = rest[:-2]
        int_part = ",".join(groups + [last3])
    else:
        int_part = f"{int(int_part):,}"

    return f"{'-' if negative else ''}{symbol}{int_part}" + (f".{dec_part}" if decimal else "")

def format_date(value, format="date"):
    if value is None:
        return "—"

    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            try:
                value = datetime.strptime(value, "%Y-%m-%d").date()
            except ValueError:
                return value

    if format == "datetime":
        return value.strftime("%d-%b-%y, %H:%M")

    if format == "slash":
        return value.strftime("%d/%m/%Y")

    return value.strftime("%d-%b-%y")

templates.env.filters["number"] = format_number
templates.env.filters["decimal"] = format_decimal
templates.env.filters["percent"] = format_percent
templates.env.filters["currency"] = format_currency
templates.env.filters["date"] = format_date

# ------------------------------------------------------------
# Application routes
# ------------------------------------------------------------
def home():
    logger.debug("\n" + "=" * 50 + " Accessed / ")
    return {
        "application": app.title,
        "version": app.version,
        "log_level": app.state.config.server.log_level,
        "log_file": app.state.config.server.log_file,
        "cache_dir": app.state.cache_dir,
        "status": "running",
    }

@app.get("/ingest")
def ingest(request: Request):
    """
    Execute transaction ingestion using the application's validated configuration and return 
    a summary suitable for the /ingest endpoint.
    Also stores the latest ingestion status in app.state.ingestion_status.    
    """
    logger.debug("\n" + "=" * 50 + " Accessed /ingest ")
    transaction_result = run_ingestion(request.app)
    tax_result = run_tax_ingestion(request.app)

    # if result["status"] == "error":
    #     return result

    return {
        "transactions": {
            "status": transaction_result["status"],
            "datasets": [
                {
                    "dataset": dataset_result["dataset"],
                    "status": dataset_result["status"],
                    "rows_read": dataset_result["rows_read"],
                    "rows_filtered": dataset_result["rows_filtered"],
                    "transactions_validated": dataset_result["transactions_validated"],
                    "transactions_retained": dataset_result.get("transactions_retained", 0),
                }
                for dataset_result in transaction_result["datasets"]
            ],
            "total_transactions": transaction_result["total_transactions"],
            "output_file": transaction_result["output_file"],
        },

        "tax": {
            "status": tax_result["status"],
            "datasets": [
                {
                    "dataset": dataset_result["dataset"],
                    "status": dataset_result["status"],
                    "rows_read": dataset_result["rows_read"],
                    "tax_validated": dataset_result["tax_validated"],
                    "tax_retained": dataset_result.get("tax_retained", 0),
                }
                for dataset_result in tax_result["datasets"]
            ],
            "total_tax_records": tax_result["total_tax_records"],
            "output_file": tax_result["output_file"],
        },
    }

@app.get("/refresh_prices")
def refresh_prices_route(request: Request, return_to: str = "/portfolio", owner: str = "ALL"):
    """
    Refresh current market prices without restarting FinMan.
    """
    logger.debug("\n" + "=" * 50 + f" Accessed /refresh_prices?Return_to={return_to}&owner={owner}")

    run_price_refresh(request.app)

    # return detail.model_dump(mode="json")
    return RedirectResponse(url=f"{return_to}?owner={owner}")

@app.get("/reingest")
def reingest(request: Request, return_url: str = "/portfolio"):
    """
    Re-ingest Excel data and refresh all dependent application data
    without restarting FinMan.
    """
    logger.debug("\n" + "=" * 50 + " Accessed /reingest")

    refresh_data(request.app)

    return RedirectResponse(url=return_url)

@app.get("/")
@app.get("/portfolio" )#, response_class=HTMLResponse)
def portfolio(request: Request, owner: str | None = None):
    logger.debug("\n" + "=" * 50 + f" Accessed /portfolio?owner={owner} ")
    
    # Extract all the owners in the transactions
    owners = request.app.state.config.frontend.owners

    # Use the configured default owner when no owner is supplied in the URL.
    if owner is None:
        owner = request.app.state.config.frontend.default_owner

    selected_owners = _resolve_owners(owner, owners)

    sections = []

    for selected_owner in selected_owners:
        owner_view = build_portfolio_view(request.app.state.transactions, request.app.state.holdings, owner=selected_owner)
        sections.extend(owner_view.sections)
    view = PortfolioView(owner=owner, sections=sections)

    all_view_holdings = [
        holding
        for section in view.sections
        for holding in section.holdings
    ]
    view_owners = {holding.owner for holding in all_view_holdings}
    view_transactions = [
        transaction
        for transaction in request.app.state.transactions
        if transaction.owner in view_owners
    ]
    overall_summary = build_portfolio_summary(all_view_holdings, view_transactions)
    view.summary = overall_summary

    # Portfolio overview shows both total and current return.
    # Current holdings already contain current return; build total holdings
    # here for display without changing the calculation layer.
    total_holdings = build_holdings(request.app.state.transactions, scope="total", owner=selected_owners)
    total_by_key = {
        (h.security, h.owner, h.asset_type, h.broker): h
        for h in total_holdings
    }

    price_status = getattr(request.app.state, "price_refresh_status", None)
    updated_at = price_status.get("updated_at") if price_status else None

    return templates.TemplateResponse(
        request,
        app.state.config.template_names.portfolio_template,
        {
            "view": view,
            "owners": owners,
            "selected_owner": owner,
            "view_url": "/portfolio",   # "/broker" for Broker
            "total_by_key": total_by_key,
            "updated_at": updated_at,
        },
    )
    # return view.model_dump(mode="json")

@app.get("/broker" )#, response_class=HTMLResponse)
def broker(request: Request, owner: str | None = None):
    logger.debug("\n" + "=" * 50 + f" Accessed /broker?owner={owner} ")

    # Extract all the owners in the transactions
    owners = request.app.state.config.frontend.owners

    # Use the configured default owner when no owner is supplied in the URL.
    if owner is None:
        owner = request.app.state.config.frontend.default_owner

    selected_owners = _resolve_owners(owner, owners)

    sections = []

    for selected_owner in selected_owners:
        owner_view = build_broker_view(request.app.state.transactions, request.app.state.holdings, owner=selected_owner,)
        sections.extend(owner_view.sections)

    view = BrokerView(owner=owner, sections=sections)

    all_view_holdings = [
        holding
        for section in view.sections
        for holding in section.holdings
    ]
    view_owners = {holding.owner for holding in all_view_holdings}
    view_transactions = [
        transaction
        for transaction in request.app.state.transactions
        if transaction.owner in view_owners
    ]
    overall_summary = build_portfolio_summary(all_view_holdings, view_transactions)
    view.summary = overall_summary

    price_status = getattr(request.app.state, "price_refresh_status", None)
    updated_at = price_status.get("updated_at") if price_status else None

    return templates.TemplateResponse(
        request,
        app.state.config.template_names.broker_template,
        {
            "view": view,
            "owners": owners,
            "selected_owner": owner,
            "view_url": "/broker",
            "updated_at": updated_at,
        },
    )
    # return view.model_dump(mode="json")

@app.get("/holding/{owner}/{asset_type}/{broker}/{security}")
def holding_detail(request: Request, owner: str, asset_type: str, broker: str, security: str, return_url: str = "/portfolio"):  
    logger.debug("\n" + "=" * 50 + f" Accessed /holding/{owner}/{asset_type}/{broker}/{security}")

    detail = build_holding_detail(request.app.state.transactions, security=security, 
                                  owner=owner, asset_type=asset_type,broker=broker)

    if detail is None:
        raise HTTPException(status_code=404, detail="Holding not found",)

    return templates.TemplateResponse(
        request,
        app.state.config.template_names.holdings_template,
        {
            "detail": detail,
            "return_url": return_url,
            "price_snapshot": request.app.state.price_snapshot,
        },
    )

@app.get("/ai/holding/{owner}/{asset_type}/{broker}/{security:path}", response_class=HTMLResponse)
async def ai_holding_page(request: Request, owner: str, asset_type: str, broker: str, 
                          security: str, return_url: str = "/portfolio" ):
    """
    Render AI analysis for one specific holding.
    """
    logger.debug("\n" + "=" * 50 + f" Accessed /ai/holding/{owner}/{asset_type}/{broker}/{security}")

    config = request.app.state.config

    if not config.ai.enabled:
        raise HTTPException(status_code=404, detail="AI features are disabled",)

    detail = build_holding_detail(request.app.state.transactions, security=security, owner=owner, 
                                  asset_type=asset_type, broker=broker)

    if detail is None:
        raise HTTPException(status_code=404, detail="Holding not found")

    holding = detail.current or detail.total or detail.closed

    if holding is None:
        raise HTTPException(status_code=404, detail="No holding data available")

    providers = await explain_holding(config.ai, holding,)

    return templates.TemplateResponse(
        request,
        config.template_names.ai_explain_template,
        {
            "detail": detail,
            "return_url": return_url,
            "price_snapshot": request.app.state.price_snapshot,
            "ai_providers": providers,
        },
    )

@app.get("/tax")
def tax(request: Request, owner: str | None = None, fy: str | None = None):
    """
    The /tax route determines the requested FY and Owner.

    - If Owner = ALL, build one Tax Portfolio view without an owner filter.

    - If a specific Owner is selected, obtain the underlying owners mapped to that UI Owner, 
      build a Tax Portfolio view for each underlying owner, and combine their sections.

    The actual construction of each Tax Portfolio view is handled by build_tax_portfolio_view().    
    """
    logger.debug("\n" + "=" * 50 + f" Accessed /tax?owner={owner}&fy={fy}")

    owners = request.app.state.config.frontend.owners
    
    # Use the configured default owner when no owner is supplied in the URL.
    if owner is None:
        owner = request.app.state.config.frontend.default_owner

    tax_transactions = request.app.state.tax

    # ----------------------------------------------------------
    # Determine available FYs from the actual tax data
    # ----------------------------------------------------------
    available_fys = sorted(
        { transaction.fy for transaction in tax_transactions },
        reverse=True,  # Get the latest at the top
    )

    if not available_fys:
        raise HTTPException(status_code=404, detail="No tax data available")

    # Default to the latest FY
    if fy is None:
        fy = available_fys[0]

    # This can happen only in debugging since the user does not directly call this route (though he can!)
    if fy not in available_fys:
        raise HTTPException(status_code=404, detail=f"FY '{fy}' not found")

    # ----------------------------------------------------------
    # Build view using the same owner-group behavior as
    # Portfolio / Broker.
    # ----------------------------------------------------------
    # if owner == "ALL":
    #     view = build_tax_portfolio_view(tax_transactions, fy=fy, owner=None,)
    # else:
    #     # The 'owner' passed is by the definition in 'frontend' config - it can be a set of owners too!
    #     selected_owners = owners[owner]
    #     sections = []
    #     for selected_owner in selected_owners:
    #         owner_view = build_tax_portfolio_view(tax_transactions, fy=fy, owner=selected_owner)
    #         sections.extend(owner_view.sections)
    #     view = TaxView(fy=fy, owner=owner, sections=sections, 
    #                    summary=build_tax_summary(
    #                    [
    #                         transaction
    #                         for section in sections
    #                         for transaction in section.transactions
    #                     ] ) )

    selected_owners = _resolve_owners(owner, owners)

    sections = []

    for selected_owner in selected_owners:
        owner_view = build_tax_portfolio_view(tax_transactions, fy=fy, owner=selected_owner)
        sections.extend(owner_view.sections)

    view = TaxView(fy=fy, owner=owner, sections=sections, 
                   summary=build_tax_summary(
                    [
                        transaction
                        for section in sections
                        for transaction in section.transactions
                    ] ) )



    return templates.TemplateResponse(
        request,
        app.state.config.template_names.tax_template,
        {
            "view": view,
            "owners": owners,
            "selected_owner": owner,
            "fys": available_fys,
            "selected_fy": fy
        },
    )
    # return view.model_dump(mode="json")

@app.get("/tax/broker")
def tax_broker(request: Request, owner: str | None = None, fy: str | None = None):
    logger.debug("\n" + "=" * 50 + f" Accessed /tax/broker?owner={owner}&fy={fy}")

    owners = request.app.state.config.frontend.owners

    # Use the configured default owner when no owner is supplied in the URL.
    if owner is None:
        owner = request.app.state.config.frontend.default_owner

    tax_transactions = request.app.state.tax

    # ----------------------------------------------------------
    # Determine available FYs
    # ----------------------------------------------------------
    available_fys = sorted(
        {
            transaction.fy
            for transaction in tax_transactions
        },
        reverse=True,
    )

    if not available_fys:
        raise HTTPException(status_code=404, detail="No tax data available")

    if fy is None:
        fy = available_fys[0]

    if fy not in available_fys:
        raise HTTPException(status_code=404, detail=f"FY '{fy}' not found")

    # ----------------------------------------------------------
    # Build Broker Tax view
    # ----------------------------------------------------------
    # if owner == "ALL":
    #     view = build_tax_broker_view(tax_transactions, fy=fy, owner=None)

    # else:
    #     selected_owners = owners[owner]
    #     sections = []
    #     for selected_owner in selected_owners:
    #         owner_view = build_tax_broker_view(tax_transactions, fy=fy, owner=selected_owner)
    #         sections.extend(owner_view.sections)
    #     view = TaxView(fy=fy, owner=owner, sections=sections,
    #                 summary=build_tax_summary(
    #                 [
    #                     transaction
    #                     for section in sections
    #                     for transaction in section.transactions
    #                 ] ) )

    selected_owners = _resolve_owners(owner, owners)

    sections = []

    for selected_owner in selected_owners:
        owner_view = build_tax_broker_view(tax_transactions, fy=fy, owner=selected_owner)
        sections.extend(owner_view.sections)

    view = TaxView(fy=fy, owner=owner, sections=sections, 
                   summary=build_tax_summary(
                    [
                        transaction
                        for section in sections
                        for transaction in section.transactions
                    ] ) )

    return templates.TemplateResponse(
        request,
        app.state.config.template_names.tax_broker_template,
        {
            "view": view,
            "owners": owners,
            "selected_owner": owner,
            "fys": available_fys,
            "selected_fy": fy
        },
    )
    # return view.model_dump(mode="json")

@app.get("/tax/holding/{fy}/{owner}/{asset_type}/{broker}/{security:path}")
def tax_holding_detail(request: Request, fy: str, owner: str, asset_type: str,
                       broker: str, security: str, return_url: str = "/tax"):
    logger.debug("\n" + "=" * 50 + f" Accessed /tax/holding/" + f"{fy}/{owner}/{asset_type}/{broker}/{security}")

    records = build_tax_detail(request.app.state.tax, fy=fy, security=security, owner=owner, 
                               asset_type=asset_type, broker=broker)

    if not records:
        raise HTTPException(status_code=404, detail="Tax holding not found")

    return templates.TemplateResponse(
        request,
        app.state.config.template_names.tax_holding_template,
        {
            "view": TaxView(
                fy=fy,
                owner=owner,
                summary=build_tax_summary(records)
            ),            
            "records": records,
            "fy": fy,
            "security": security,
            "owner": owner,
            "asset_type": asset_type,
            "broker": broker,
            "return_url": return_url,
            "owners": request.app.state.config.frontend.owners,
            "selected_owner": owner,
            "selected_fy": fy,
            "fys": sorted(
                {
                    transaction.fy
                    for transaction in request.app.state.tax
                },
                reverse=True,
            ),
            "view_url": return_url.split("?")[0],
                },
    )
    # return {
    #     "fy": fy,
    #     "security": security,
    #     "owner": owner,
    #     "asset_type": asset_type,
    #     "broker": broker,
    #     "records": [
    #         record.model_dump(mode="json")
    #         for record in records
    #     ],
    # }

# ------------------------------------------------------------
# Settings helpers
# ------------------------------------------------------------
SETTINGS_SECTIONS = {"frontend", "ai", "external_links"}
MASKED_KEYWORDS = {"api_key", "password", "secret"}
MASKED_VALUE = "********"
EDITABLE_SECTIONS = [
    {
        "name": "frontend",
        "title": "Frontend",
        "description": "Owner and asset-type selections used by the application.",
    },
    {
        "name": "ai",
        "title": "AI",
        "description": "AI enablement, providers, models and provider settings.",
    },
    {
        "name": "external_links",
        "title": "External Links",
        "description": "External research links and their URL templates.",
    },
]

MASKED_VALUE = "********"

def _settings_config_file():
    return BASE_DIR / "config.json"

def _mask_secrets(value):
    if isinstance(value, dict):
        return {
            key: (
                MASKED_VALUE
                if key in MASKED_KEYWORDS
                else _mask_secrets(item)
            )
            for key, item in value.items()
        }

    if isinstance(value, list):
        return [_mask_secrets(item) for item in value]

    return value

def _format_config_json(data):
    """Format config JSON compactly where values are small and simple."""
    COMPACT_MAX_LENGTH = 100  # If a nested structure can fit in approximately 100 characters, keep it on one line.

    # Return FALSE if it is a dictionary or list i.e what we call a scalar
    # "Ticker" → True, ["RSN"] → False, {"a": "b"} → False
    def is_scalar(value):
        return not isinstance(value, (dict, list))

    # Wrapper around Python's existing JSON encoder. Add space between mentioned seperators
    # between items: , between key and value: : + space
    # ensure_ascii=False, asks python not to represent Unicode characters using escaped sequences.
    def compact_json(value):
        return json.dumps(value, ensure_ascii=False, separators=(",", ": "))

    # Checks if I wrote this entire thing on one line, would it still be reasonably short?
    # level is the depth of indentation (starting at 0)
    def should_compact(value, level):
        # Never compact the root object.
        if level == 0:
            return False
        # Scalars are naturally compact.
        if is_scalar(value):
            return True
        # Try the entire object/list in compact form.
        compact = compact_json(value)
        # Keep short structures on one line.
        return len(compact) <= COMPACT_MAX_LENGTH

    def format_value(value, level=0):
        indent = "  " * level  # Indentation for the current level increases with level number
        next_indent = "  " * (level + 1)  # Indentation for the next level is +1 to the previous level
        # Scalars
        if is_scalar(value):
            return json.dumps(value, ensure_ascii=False)
        # Compact small dictionaries/lists
        if should_compact(value, level):
            return compact_json(value)
        # Lists
        if isinstance(value, list):
            if not value:
                return "[]"
            lines = ["["]
            for i, item in enumerate(value):
                formatted = format_value(item, level + 1)
                lines.append(f"{next_indent}{formatted}" + ("," if i < len(value) - 1 else ""))
            lines.append(f"{indent}]")
            return "\n".join(lines)
        # Dictionaries
        lines = ["{"]
        items = list(value.items())
        for i, (key, val) in enumerate(items):
            formatted = format_value(val, level + 1)
            lines.append(
                f'{next_indent}{json.dumps(key, ensure_ascii=False)}: '
                f'{formatted}' + ("," if i < len(items) - 1 else "") )
        lines.append(f"{indent}}}") # Attach the final } for the dictionary
        return "\n".join(lines) # Convert the list to a string with newlines between each line froming the dictionary

    return format_value(data)

def _save_settings_section(section, section_data, config):
    if section not in SETTINGS_SECTIONS:
        raise ValueError("This configuration section cannot be edited.")

    # Preserve existing secrets when the masked value is submitted.
    if section == "ai":
        current_ai = config.ai.model_dump(mode="json")

        for provider_name, provider_data in section_data.get("providers", {}).items():
            current_provider = current_ai.get("providers", {}).get(provider_name, {})
            if provider_data.get("api_key") == MASKED_VALUE:
                if "api_key" in current_provider:
                    provider_data["api_key"] = current_provider["api_key"]

    # Validate the complete proposed effective configuration before saving.
    proposed_config = config.model_dump(mode="json", by_alias=True) # Use field aliases so config JSON keys such as "and" are preserved.
    proposed_config[section] = section_data
    FinManConfig.model_validate(proposed_config)

    config_file = _settings_config_file()

    override_config = read_json(config_file, default={})

    override_config[section] = section_data

    temp_file = config_file.with_suffix(".tmp")

    temp_file.write_text(_format_config_json(override_config) + "\n", encoding="utf-8")

    temp_file.replace(config_file)

    return load_config(BASE_DIR)

@app.get("/settings", response_class=HTMLResponse)
async def settings(request: Request, return_url: str = "/portfolio", saved: str | None = None, error: str | None = None):
    """
    Display the effective FinMan configuration and allow selected
    configuration sections to be edited.
    """
    logger.debug("\n" + "=" * 50 + " Accessed /settings")

    config = request.app.state.config
    effective_config = config.model_dump(mode="json")

    return templates.TemplateResponse(
        request,
        config.template_names.settings_template,
        {
            "config": _mask_secrets(effective_config),
            "environment": get_config_environment(),
            "override_file": _settings_config_file().name,
            "return_url": return_url,
            "saved": saved,
            "error": error,
            "editable_sections": EDITABLE_SECTIONS,            
        },
    )

@app.get("/settings/edit/{section}", response_class=HTMLResponse)
async def settings_edit(request: Request, section: str, return_url: str = "/portfolio"):
    """
    Display the common configuration editor for one editable section.
    """
    logger.debug("\n" + "=" * 50 + f" Accessed /settings/edit/{section}")

    if section not in SETTINGS_SECTIONS:
        raise HTTPException(status_code=404, detail="Configuration section not found.")

    config = request.app.state.config

    section_info = next(
        item for item in EDITABLE_SECTIONS
        if item["name"] == section
    )

    section_data = getattr(config, section).model_dump(mode="json")
    section_data = _mask_secrets(section_data)

    return templates.TemplateResponse(
        request,
        "settings_editor.html",
        {
            "section": section,
            "section_title": section_info["title"],
            "section_description": section_info["description"],
            "section_data": section_data,
            "return_url": return_url,
        },
    )

@app.post("/settings/edit/{section}", response_class=HTMLResponse)
async def save_settings_edit(request: Request, section: str):
    """
    Save one editable configuration section to the active
    environment-specific configuration file.
    """
    logger.debug("\n" + "=" * 50 + f" Saving /settings/edit/{section}")

    body = await request.body()
    form = parse_qs(body.decode("utf-8"))

    content = form.get("content", [""])[0]
    return_url = form.get("return_url", ["/portfolio"])[0]

    try:
        if section not in SETTINGS_SECTIONS:
            raise ValueError("Invalid configuration section.")

        section_data = json.loads(content)

        if not isinstance(section_data, dict):
            raise ValueError("Configuration section must contain a JSON object.")

        request.app.state.config = _save_settings_section(section, section_data, request.app.state.config)
        configure_external_links(request.app.state.config.model_dump())  # Do this explicitly since this is done within the normal startup lifecycle.

        return RedirectResponse(url=(f"/settings?return_url={quote(return_url, safe='')}" f"&saved={quote(section, safe='')}"), 
                                status_code=303)

    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        logger.exception("Failed to save settings")

        return RedirectResponse(
            url=(f"/settings?return_url={quote(return_url, safe='')}" f"&error={quote(str(exc), safe='')}"), 
            status_code=303)

@app.get("/status", response_class=HTMLResponse)
async def status(request: Request, return_url: str = "/portfolio",):
    """
    Render the current application ingestion status for the status page.
    Displays the most recently recorded ingestion result stored in app.state.
    """
    logger.debug("\n" + "=" * 50 + " Accessed /status ")

    # Get the ingestion and prices refresh statusus
    ingestion_status = getattr(request.app.state, "ingestion_status", None)
    tax_ingestion_status = getattr(request.app.state, "tax_ingestion_status", None)
    price_refresh_status = getattr(request.app.state, "price_refresh_status", None)

    ai_status = await check_ai_health(request.app.state.config.ai)

    return templates.TemplateResponse(
        request,
        app.state.config.template_names.status_template,
        {
            "status": ingestion_status,
            "tax_status": tax_ingestion_status,
            "price_status": price_refresh_status,
            "ai_status": ai_status,
            "return_url" : return_url,
        },
    )


# ------------------------------------------------------------
# Server startup
# ------------------------------------------------------------
def main() -> None:
    """
    Start the FinMan Uvicorn server using the validated server configuration.

    Host and port come from the validated FinMan configuration.
    Development-only reload options are supplied through command-line arguments.
    """
    parser = argparse.ArgumentParser(description="Start FinMan")

    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable Uvicorn auto-reload for development.",
    )

    parser.add_argument(
        "--reload-dir",
        action="append",
        default=None,
        help="Directory to watch for reloads.",
    )

    parser.add_argument(
        "--reload-include",
        action="append",
        default=None,
        help="File pattern to include in reload watching.",
    )

    parser.add_argument(
        "--reload-exclude",
        action="append",
        default=None,
        help="File pattern to exclude from reload watching.",
    )

    args = parser.parse_args()

    config = load_config(BASE_DIR)

    uvicorn.run(
        "finman:app",
        host=config.server.host,
        port=config.server.port,
        reload=args.reload,
        reload_dirs=args.reload_dir,
        reload_includes=args.reload_include,
        reload_excludes=args.reload_exclude,
    )

if __name__ == "__main__":
    main()