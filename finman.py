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
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

# SessionMiddleware provides the browser session used by FinMan
# authentication. The authentication helpers are kept separately in auth.py.
from starlette.middleware.sessions import SessionMiddleware
from auth import authenticate, get_auth_config, require_login

from logger import configure_logger, logger
from ingestion.config import load_config, get_config_environment
from ingestion.transactions import ingest_transactions
from market.prices import refresh_prices
from market.external_links import configure_external_links, load_mf_master
from ingestion.tax_ingestion import ingest_tax
from ingestion.json_io import read_json, write_json
from ingestion.consolidated import ingest_consolidated
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

from settings import (
    get_settings_file,
    read_settings_file, 
    get_settings_navigation,
    get_settings_section,
    mask_secrets,
    SETTINGS_FILES,
    save_settings_section,
    format_settings_for_display
)

# ------------------------------------------------------------
# Mutual Fund Research
# ------------------------------------------------------------
# The research module owns the cached MF analytics.  finman.py
# only exposes those analytics through the authenticated web route.
from research.research import ( 
    configure_research, 
    get_research, 
    load_research_index, 
    run_research_refresh
)

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
            "updated_at": datetime.now().isoformat()
        }
        return result  # Returns the status and transactions
    except ValueError as exc:
        app.state.ingestion_status = {"status": "error", "message": str(exc)}
        raise

def run_consolidated_ingestion(app):
    config = app.state.config

    output_file = (Path(app.state.cache_dir) / config.cache.consolidated_file)

    result = ingest_consolidated(dataset=config.consolidateddata, output_file=output_file)

    app.state.consolidated = result["holdings"]

    app.state.consolidated_ingestion_status = {
        "status": result["status"],
        "rows_read": result["rows_read"],
        "rows_filtered": result["rows_filtered"],
        "holdings_validated": result["holdings_validated"],
        "output_file": result["output_file"],
    }

    return result

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
            "updated_at": datetime.now().isoformat()
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
    run_consolidated_ingestion(app)
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
    5. Configure the external links for the templates use
    6. Refresh data:
        Ingest transactions
        ingest consolidate data
        Ingest tax records
        Update market prices
        Build current holdings
    7. Configure and run research refresh
    8. Yield control to the running application.       
    """
    # 1. Load and validate configuration.
    app.state.base_dir = BASE_DIR
    app.state.config = load_config(BASE_DIR)

    # 2. Register development-only debug routes when debug mode is enabled
    if app.state.config.server.debug:
        app.include_router(debug_router)

    # 3. Configure logging using the validated configuration.
    configure_logger(app.state.config.server.log_level, app.state.config.server.log_file)

    # 4. Initialize the cache directory.
    cache_dir = Path(app.state.config.server.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    app.state.cache_dir = cache_dir

    # 5. Initialize external-link URL templates from the same validated
    # configuration used by the rest of FinMan.
    configure_external_links(app.state.config.model_dump())

    # 6. Run transaction ingestion automatically at startup.
    #    Run consolidated data ingestion at startup
    #    Run tax ingestion automatically at startup.
    #    Refresh current market prices.
    #    Build current holdings using the enriched transaction data.
    refresh_data(app)

    # 7. Initialize Research using the same validated configuration
    # used by the rest of FinMan.
    configure_research(app.state.config, BASE_DIR)
    app.state.research_status = run_research_refresh()

    # 8. Application is ready to serve requests.
    yield

    # Shutdown work can be added here later (after yield).

# ------------------------------------------------------------
# Application initialization
# ------------------------------------------------------------
app = FastAPI(title="FinMan", version="0.1.0", lifespan=lifespan)

# ------------------------------------------------------------
# Jinja globals
# ------------------------------------------------------------
def price_updated_at():
    """Return the timestamp of the most recent successful price refresh."""
    price_status = getattr(app.state, "price_refresh_status", None)
    return price_status.get("updated_at") if price_status else None

def usd_inr_rate():
    """Return the exchange rate of the most recent successful price refresh."""
    price_status = getattr(app.state, "price_refresh_status", None)
    if not price_status:
        return None
    exchange_rates = price_status.get("exchange_rates", {})
    return exchange_rates.get("USDINR")

templates.env.globals["price_updated_at"] = price_updated_at
templates.env.globals["usd_inr_rate"] = usd_inr_rate

# Authentication / browser session
# FinMan uses a signed browser session to remember successful login.
#
# SessionMiddleware stores the session data in a browser cookie and uses FINMAN_SESSION_SECRET to sign the cookie.
# The secret is kept separate from the FinMan login password and is read from the environment rather than config.json.
#
# Authentication is applied to individual application routes using Depends(require_login).
# The /login and /logout routes remain public.
_, session_secret = get_auth_config()
app.add_middleware(SessionMiddleware, secret_key=session_secret, same_site="lax", https_only=False)  # FinMan currently runs over HTTP on the LAN.

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
# Authentication routes
# ------------------------------------------------------------
# These routes intentionally remain public:
#
#   GET  /login   - Display the login page.
#   POST /login   - Validate the password and create an authenticated session.
#   GET  /logout  - Clear the authenticated session.
#
# All other protected application routes use Depends(require_login).
# The authentication implementation itself is kept in auth.py.

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """
    Display the FinMan login page.

    The optional "error" query parameter is used to display a login failure message. The optional "next" parameter 
    identifies the page the user originally requested and allows FinMan to return there after successful authentication.
    """    
    error = request.query_params.get("error")
    next_url = request.query_params.get("next", "/dashboard")

    return templates.TemplateResponse(
        request,
        request.app.state.config.template_names.login_template,
        {
            "request": request,
            "error": error,
            "next_url": next_url,
        },
    )

@app.post("/login")
async def login(request: Request):
    """
    Authenticate the user and establish the FinMan browser session.

    The login form supplies a password and an optional "next" URL. On successful authentication, the route sets
    the "authenticated" session flag and redirects the user to the originally requested page.

    A 303 redirect is used after the POST so that the browser performs the destination request as a GET.

    The "next" URL is restricted to an internal relative URL to prevent an open-redirect vulnerability.
    """    
    body = await request.body()
    form = parse_qs(body.decode("utf-8"))

    password = form.get("password", [""])[0]
    next_url = form.get("next", ["/dashboard"])[0]

    # Only allow internal relative URLs as the post-login destination. This prevents an attacker from 
    # supplying an external URL through the "next" parameter.    
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/dashboard"

    # Validate the supplied password using the authentication helper.
    # authenticate() performs the actual credential comparison.
    if not authenticate(password):
        return RedirectResponse(url=f"/login?error=Invalid+password&next={quote(next_url)}", status_code=303)

    # Mark the browser session as authenticated. SessionMiddleware persists this session information in the signed browser cookie.
    request.session["authenticated"] = True

    # Return the user to the page they originally requested.
    return RedirectResponse(url=next_url, status_code=303)

@app.get("/logout")
async def logout(request: Request):
    """
    Log the user out by clearing the current browser session.

    After the session is cleared, the user is redirected to the login page. Any subsequently protected route
    will require authentication again.
    """    
    request.session.clear()

    return RedirectResponse(url="/login", status_code=303)

# ------------------------------------------------------------
# Application routes
# ------------------------------------------------------------
# Application routes are protected using the require_login dependency.
#
# The dependency checks the authenticated session created by /login. Unauthenticated requests are 
# redirected to /login and, after successful authentication, returned to the originally requested URL.
#
# Public routes are limited to the authentication endpoints above.

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

@app.get("/ingest", dependencies=[Depends(require_login)])
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

@app.get("/refresh_prices", dependencies=[Depends(require_login)])
def refresh_prices_route(request: Request, return_to: str = "/portfolio", owner: str = "ALL"):
    """
    Refresh current market prices without restarting FinMan.
    """
    logger.debug("\n" + "=" * 50 + f" Accessed /refresh_prices?Return_to={return_to}&owner={owner}")

    run_price_refresh(request.app)

    # return detail.model_dump(mode="json")
    return RedirectResponse(url=f"{return_to}?owner={owner}")

@app.get("/reingest", dependencies=[Depends(require_login)])
def reingest(request: Request, return_url: str = "/portfolio"):
    """
    Re-ingest Excel data and refresh all dependent application data
    without restarting FinMan.
    """
    logger.debug("\n" + "=" * 50 + " Accessed /reingest")

    refresh_data(request.app)

    return RedirectResponse(url=return_url)

@app.get("/portfolio", dependencies=[Depends(require_login)])#, response_class=HTMLResponse)
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

    return templates.TemplateResponse(
        request,
        request.app.state.config.template_names.portfolio_template,
        {
            "view": view,
            "owners": owners,
            "selected_owner": owner,
            "view_url": "/portfolio",   # "/broker" for Broker
            "total_by_key": total_by_key
        },
    )
    # return view.model_dump(mode="json")

@app.get("/broker", dependencies=[Depends(require_login)])#, response_class=HTMLResponse)
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

    return templates.TemplateResponse(
        request,
        request.app.state.config.template_names.broker_template,
        {
            "view": view,
            "owners": owners,
            "selected_owner": owner,
            "view_url": "/broker"
        },
    )
    # return view.model_dump(mode="json")

@app.get("/holding/{owner}/{asset_type}/{broker}/{security}", dependencies=[Depends(require_login)])
def holding_detail(request: Request, owner: str, asset_type: str, broker: str, security: str, return_url: str = "/portfolio"):  
    """
    Display detailed transaction and holding information for one security.
    """    
    logger.debug("\n" + "=" * 50 + f" Accessed /holding/{owner}/{asset_type}/{broker}/{security}")

    detail = build_holding_detail(request.app.state.transactions, security=security, 
                                  owner=owner, asset_type=asset_type,broker=broker)

    if detail is None:
        raise HTTPException(status_code=404, detail="Holding not found",)

    return templates.TemplateResponse(
        request,
        request.app.state.config.template_names.holdings_template,
        {
            "detail": detail,
            "return_url": return_url,
            "price_snapshot": request.app.state.price_snapshot,
        },
    )

@app.get("/ai/holding/{owner}/{asset_type}/{broker}/{security:path}", response_class=HTMLResponse,
         dependencies=[Depends(require_login)])
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
        request.app.state.config.template_names.ai_explain_template,
        {
            "detail": detail,
            "return_url": return_url,
            "price_snapshot": request.app.state.price_snapshot,
            "ai_providers": providers,
        },
    )

@app.get("/tax", dependencies=[Depends(require_login)])
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
        request.app.state.config.template_names.tax_template,
        {
            "view": view,
            "owners": owners,
            "selected_owner": owner,
            "fys": available_fys,
            "selected_fy": fy
        },
    )
    # return view.model_dump(mode="json")

@app.get("/tax/broker", dependencies=[Depends(require_login)])
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
        request.app.state.config.template_names.tax_broker_template,
        {
            "view": view,
            "owners": owners,
            "selected_owner": owner,
            "fys": available_fys,
            "selected_fy": fy
        },
    )
    # return view.model_dump(mode="json")

@app.get("/tax/holding/{fy}/{owner}/{asset_type}/{broker}/{security:path}", dependencies=[Depends(require_login)])
def tax_holding_detail(request: Request, fy: str, owner: str, asset_type: str,
                       broker: str, security: str, return_url: str = "/tax"):
    logger.debug("\n" + "=" * 50 + f" Accessed /tax/holding/" + f"{fy}/{owner}/{asset_type}/{broker}/{security}")

    records = build_tax_detail(request.app.state.tax, fy=fy, security=security, owner=owner, 
                               asset_type=asset_type, broker=broker)

    if not records:
        raise HTTPException(status_code=404, detail="Tax holding not found")

    return templates.TemplateResponse(
        request,
        request.app.state.config.template_names.tax_holding_template,
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

@app.get("/", dependencies=[Depends(require_login)])
@app.get("/dashboard", response_class=HTMLResponse, dependencies=[Depends(require_login)])
def dashboard(request: Request, owner: str | None = None):
    """
    Render the Portfolio Dashboard.
    The dashboard combines two independent data sources:
    1. Consolidated investments
       - Already summarized investments such as PPF, EPF, Bonds, etc.
       - Loaded into app.state.consolidated during application startup/reingest.
       - These records are displayed directly; they do not go through the
         transaction/price-refresh/portfolio calculation pipeline.
    2. Shares & Mutual Funds
       - Uses the existing portfolio calculation layer.
       - build_portfolio_view() creates sections grouped by asset type and owner.
       - The dashboard uses only the section summaries and aggregates them by asset type.
       - No separate portfolio calculation logic is introduced here.

    Owner handling follows the same configuration used by Portfolio/Broker:
        - Individual owner -> that owner's data
        - Grouped owner    -> all underlying owners in the group
        - ALL              -> all owners
    """    
    logger.debug("\n" + "=" * 50 + f" Accessed /dashboard?owner={owner}")

    # ------------------------------------------------------------
    # Owner selection
    # ------------------------------------------------------------
    # Get the configured UI owners.  These may represent either
    # individual owners or groups of underlying owners.
    owners = request.app.state.config.frontend.owners

    # Use the configured default owner when no owner is supplied.
    if owner is None:
        owner = request.app.state.config.frontend.default_owner

    # Resolve the UI owner into the actual underlying owner IDs.
    #
    # For example:
    #   "RSN"   -> ["RSN"]
    #   "FAMILY" -> ["RSN", "MALT"]
    #   "ALL"   -> [None]
    selected_owners = _resolve_owners(owner, owners)

    # ----------------------------------------------------------
    # Consolidated investments
    #
    # When ALL is selected, _resolve_owners() returns [None].
    # In that case no owner filtering is required.
    # ----------------------------------------------------------
    if None in selected_owners:
        consolidated = request.app.state.consolidated
    else:
        consolidated = [
            holding
            for holding in request.app.state.consolidated
            if holding.owner in selected_owners
        ]

    # ------------------------------------------------------------
    # Shares / Mutual Funds summary
    # ------------------------------------------------------------
    # Reuse the existing Portfolio calculation layer.
    #
    # build_portfolio_view() already:
    #   - filters holdings by owner
    #   - groups them into portfolio sections
    #   - calculates the summary for each section
    #
    # The dashboard does not need the individual holdings.  It only needs those section summaries.
    sections = []

    for selected_owner in selected_owners:
        owner_view = build_portfolio_view(request.app.state.transactions, request.app.state.holdings, owner=selected_owner)
        sections.extend(owner_view.sections)

    # ------------------------------------------------------------
    # Aggregate Shares / MF sections by asset type
    # ------------------------------------------------------------
    # Portfolio sections are normally separated by asset type AND owner.  
    # The dashboard combines those sections so that there is one row per asset type:
    #     MF(Equity)
    #     MF(Debt)
    #     ...
    # This means the dashboard's "Asset Type" totals represent the total for that asset type across all selected owners.
    shares_mf_summary = {}

    for section in sections:
        summary = section.summary

        # Create the aggregate row the first time this asset type is encountered.
        if section.asset_type not in shares_mf_summary:
            shares_mf_summary[section.asset_type] = {
                "asset_type": section.asset_type,
                "invested_value": 0,
                "current_value": 0,
                "gain": 0,
                "gain_pct": None,
            }

        item = shares_mf_summary[section.asset_type]

        # Add the section's values to the asset-type total.
        item["invested_value"] += summary.cost
        item["current_value"] += summary.net_value
        item["gain"] += summary.gain_value

    # ------------------------------------------------------------
    # Calculate aggregate gain percentage
    # ------------------------------------------------------------
    # Gain percentage must be calculated from the aggregated values,
    # rather than averaging the individual section percentages.
    #
    # Example:
    #     total gain % = total gain / total invested value
    for item in shares_mf_summary.values():
        if item["invested_value"]:
            item["gain_pct"] = item["gain"] / item["invested_value"]

    # Jinja iterates over lists, whereas the aggregation above uses a dictionary keyed by asset type.
    # Convert the dictionary values into a list for the template.
    shares_mf_summary = list(shares_mf_summary.values())

    # ------------------------------------------------------------
    # Render dashboard
    # ------------------------------------------------------------
    return templates.TemplateResponse(
        request,
        request.app.state.config.template_names.dashboard_template,
        {
            "owners": owners,
            "selected_owner": owner,
            "consolidated": consolidated,
            "shares_mf_summary": shares_mf_summary,
        },
    )

@app.get("/settings", response_class=HTMLResponse, dependencies=[Depends(require_login)])
async def settings(request: Request, file: str = "config", section: str | None = None,
                   return_url: str = "/portfolio", saved: str | None = None, error: str | None = None):
    """Display a selected JSON settings file and section."""
    logger.debug("\n" + "=" * 50 + " Accessed /settings")

    settings_file = get_settings_file(file)

    data = read_settings_file(file)

    navigation = get_settings_navigation(data)

    # Select the first top-level section if none was requested.
    if section is None and navigation:
        section = navigation[0]["key"]

    section_data = get_settings_section(data, section) if section else data
    section_display = format_settings_for_display(mask_secrets(section_data))

    editable = bool(section and section in settings_file["editable"])

    return templates.TemplateResponse(
        request,
        request.app.state.config.template_names.settings_template,
        {
            "settings_files": SETTINGS_FILES,
            "selected_file": file,
            "selected_file_title": settings_file["title"],
            "navigation": navigation,
            "selected_section": section,
            "section_data": section_display,
            "section_editable": editable,
            "environment": get_config_environment(),
            "override_file": settings_file["title"],
            "return_url": return_url,
            "saved": saved,
            "error": error,
        },
    )

@app.get("/settings/edit/{file}/{section:path}", response_class=HTMLResponse, dependencies=[Depends(require_login)])
async def settings_edit(request: Request, file: str, section: str, return_url: str = "/settings"):
    """Display the generic JSON editor for one editable section."""
    logger.debug("\n" + "=" * 50 + f" Accessed /settings/edit/{file}/{section}")

    settings_file = get_settings_file(file)

    if section not in settings_file["editable"]:
        raise HTTPException(status_code=403, detail="This settings section is read-only.",)

    data = read_settings_file(file)

    section_data = get_settings_section(data, section)

    return templates.TemplateResponse(
        request,
        request.app.state.config.template_names.settings_editor_template,
        {
            "file": file,
            "file_title": settings_file["title"],
            "section": section,
            "section_title": section.split(".")[-1].replace("_", " ").title(),
            "section_description": f"Edit {section}.",
            "section_data": mask_secrets(section_data),
            "return_url": return_url,
        },
    )

@app.post("/settings/edit/{file}/{section:path}", response_class=HTMLResponse, dependencies=[Depends(require_login)])
async def save_settings_edit(request: Request, file: str, section: str):
    """Save one editable section of a JSON settings file."""
    logger.debug("\n" + "=" * 50 + f" Saving /settings/edit/{file}/{section}")

    # The raw text from the ofrm is recevied and parsed into a string
    body = await request.body()
    form = parse_qs(body.decode("utf-8")) # body.decode turns the bytes into text and parse converts it to dict like structure
 
    content = form.get("content", [""])[0]  # extracts the JSON text from the form content. content is still a string here
    return_url = form.get("return_url", ["/settings"])[0]

    try:
        section_data = json.loads(content)   # converts a json text to python dictionart

        if not isinstance(section_data, dict):
            raise ValueError("Configuration section must contain a JSON object.")

        request.app.state.config = save_settings_section(file, section, section_data, request.app.state.config)

        # Only config files affect the live FinMan configuration.
        if file in {"config", "config_dev", "config_prod"}:
            configure_external_links(request.app.state.config.model_dump())

        return RedirectResponse(
            url=(
                f"/settings?"
                f"file={quote(file, safe='')}"
                f"&section={quote(section, safe='')}"
                f"&return_url={quote(return_url, safe='')}"
                f"&saved={quote(section, safe='')}"
            ),
            status_code=303,
        )

    except (json.JSONDecodeError, ValueError, TypeError) as exc:

        logger.exception("Failed to save settings")

        return RedirectResponse(
            url=(
                f"/settings?"
                f"file={quote(file, safe='')}"
                f"&section={quote(section, safe='')}"
                f"&return_url={quote(return_url, safe='')}"
                f"&error={quote(str(exc), safe='')}"
            ),
            status_code=303,
        )
    
@app.get("/status", response_class=HTMLResponse, dependencies=[Depends(require_login)])
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

    research_status = getattr(request.app.state, "research_status", None)

    return templates.TemplateResponse(
        request,
        request.app.state.config.template_names.status_template,
        {
            "status": ingestion_status,
            "tax_status": tax_ingestion_status,
            "price_status": price_refresh_status,
            "research_status": research_status,
            "ai_status": ai_status,
            "return_url" : return_url,
        },
    )

# ------------------------------------------------------------
# Mutual Fund Research
# ------------------------------------------------------------
def build_research_viewmodel(fund, *, generated_at: str | None = None):
    """
    Convert the Research model into the presentation structure expected by research.html.

    No calculations are performed here.
    """
    # --------------------------------------------------------
    # Identity
    # --------------------------------------------------------
    full_name = fund.fund_name
    display_name = full_name
    subtitle = ""

    if " - " in full_name:
        parts = full_name.split(" - ")
        display_name = parts[0]
        subtitle = " • ".join(parts[1:])

    vm = {
        "fund_name": full_name,
        "display_name": display_name,
        "subtitle": subtitle,
        "amfi_code": fund.amfi_code,
        "generated_at": generated_at,
    }

    # --------------------------------------------------------
    # Performance
    # --------------------------------------------------------
    vm["performance"] = {
        "return_1m": fund.return_1m,
        "return_3m": fund.return_3m,
        "return_6m": fund.return_6m,
        "cagr_1y": fund.cagr_1y,
        "cagr_3y": fund.cagr_3y,
        "cagr_5y": fund.cagr_5y,
        "cagr_7y": fund.cagr_7y,
        "cagr_10y": fund.cagr_10y,
    }

    # --------------------------------------------------------
    # Risk
    # --------------------------------------------------------
    drawdown = fund.drawdown
    volatility = fund.volatility

    vm["risk"] = {
        "volatility": (
            volatility.volatility
            if volatility else None
        ),
        "observations": (
            volatility.observations
            if volatility else None
        ),
        "max_drawdown": (
            drawdown.max_drawdown
            if drawdown else None
        ),
        "current_drawdown": (
            drawdown.current_drawdown
            if drawdown else None
        ),
        "peak_nav": (
            drawdown.peak_nav
            if drawdown else None
        ),
        "trough_nav": (
            drawdown.trough_nav
            if drawdown else None
        ),
        "peak_date": (
            drawdown.peak_date
            if drawdown else None
        ),
        "trough_date": (
            drawdown.trough_date
            if drawdown else None
        ),
    }

    # --------------------------------------------------------
    # Rolling Returns
    # --------------------------------------------------------
    rolling = []

    for period, result in [
        ("1M", fund.rolling_1m),
        ("3M", fund.rolling_3m),
        ("6M", fund.rolling_6m),
        ("1Y", fund.rolling_1y),
        ("3Y", fund.rolling_3y),
        ("5Y", fund.rolling_5y),
        ("7Y", fund.rolling_7y),
        ("10Y", fund.rolling_10y),        
    ]:
        rolling.append({
            "period": period,
            "available": result is not None,
            "min_return": (
                result.min_return if result else None
            ),
            "avg_return": (
                result.avg_return if result else None
            ),
            "median_return": (
                result.median_return if result else None
            ),
            "max_return": (
                result.max_return if result else None
            ),
            "observations": (
                result.observations if result else None
            ),
        })

    vm["rolling"] = rolling

    # --------------------------------------------------------
    # Calendar Returns
    # --------------------------------------------------------
    calendar = []

    if fund.calendar_returns:
        for result in sorted(
            fund.calendar_returns,
            key=lambda x: x.year,
            reverse=True,
        ):
            calendar.append({
                "year": result.year,
                "return_pct": result.return_pct,
                "start_nav": result.start_nav,
                "end_nav": result.end_nav,
            })

    vm["calendar_returns"] = calendar

    # --------------------------------------------------------
    # Summary cards
    # --------------------------------------------------------
    vm["summary"] = [
        {
            "label": "1 Year CAGR",
            "value": fund.cagr_1y,
            "class": (
                "positive"
                if fund.cagr_1y is not None
                and fund.cagr_1y >= 0
                else "negative"
            ),
        },
        {
            "label": "3 Year CAGR",
            "value": fund.cagr_3y,
            "class": (
                "positive"
                if fund.cagr_3y is not None
                and fund.cagr_3y >= 0
                else "negative"
            ),
        },
        {
            "label": "5 Year CAGR",
            "value": fund.cagr_5y,
            "class": (
                "positive"
                if fund.cagr_5y is not None
                and fund.cagr_5y >= 0
                else "negative"
            ),
        },
    ]

    return vm

@app.get("/refresh_research", response_class=HTMLResponse, dependencies=[Depends(require_login)])
async def refresh_research_route(request: Request, return_to: str = "/portfolio", owner: str = ""):
    logger.debug("\n" + "=" * 50 + " Refreshing Research")

    request.app.state.research_status = run_research_refresh()

    redirect_url = return_to
    if owner:
        redirect_url += f"?owner={owner}"

    return RedirectResponse(url=redirect_url, status_code=303)

@app.get("/research/{amfi_code}", response_class=HTMLResponse, dependencies=[Depends(require_login)])
async def research_dashboard(request: Request, amfi_code: str, return_url: str = "/portfolio"):
    """
    Display cached research for a mutual fund identified by AMFI code.

    AMFI code is the canonical identifier used by the FinMan
    research subsystem. The route reads the existing research
    cache and does not refresh NAV history.
    """
    logger.debug("\n" + "=" * 50 + f" Accessed /research/{amfi_code}")

    # Get Research for this fund.
    # If it is not already cached, get_research() will download the NAV history, calculate the metrics,
    # and add it to the Research index.
    fund = get_research(amfi_code)

    research = load_research_index()
    # Convert the internal FundResearch model into the structure expected by research.html.
    generated = research.generated_at

    try:
        generated = datetime.fromisoformat(generated).strftime("%d %b %Y %H:%M")
    except Exception:
        pass

    vm = build_research_viewmodel(fund, generated_at=generated)

    return templates.TemplateResponse(
        request,
        request.app.state.config.template_names.research_template,
        {
            "fund": vm,
            "return_url": return_url,
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