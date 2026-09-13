"""
External research links for portfolio holdings.

DESIGN PRINCIPLES (NON-NEGOTIABLE):
- Only generate links when identifiers match exactly.
- Never guess or fake deep links.
- Prefer canonical, symbol-based URLs.
- Zero dead links, zero redirects, zero surprises.
"""
import json, os
from pathlib import Path
from typing import Dict

# Project root = one level above /market
BASE_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = BASE_DIR / "static"

# -------------------------------------------------------------------------------------
# STOCKLoad the MF Names to Value Research / Morning Star / Google finance links
# --------------------------------------------------------------------------------------
_MF_MASTER: Dict[str, dict] | None = None
# External-link URL templates loaded once from the application's
# already-validated configuration.
_EXTERNAL_LINK_CONFIG: Dict[str, dict] = {}

# -------------------------------------------------
# HELPER FUNCTIONS
# -------------------------------------------------
def configure_external_links(config: dict) -> None:
    """
    Initialize the external-link config once at application startup.

    FinMan loads and validates the complete configuration in finman.py. We only retain the external_links
    section here so the rest of the application does not need to pass config through the portfolio layer.
    """
    global _EXTERNAL_LINK_CONFIG
    _EXTERNAL_LINK_CONFIG = config.get("external_links", {})

def load_mf_master() -> Dict[str, dict]:
    """
    This loads the master list of mutual fund names and their corresponding identifiers for Value Research,
    Morningstar, and Google Finance. On mf_links() calls, we look up the fund name in this master list to
    get the identifiers needed to generate the external links.

    This also provides the same identifiers for refresh prices to extract live prices data from AMFI.
    """
    global _MF_MASTER

    # Read the mf_master.json file only once and cache it in memory. This avoids repeated disk I/O and JSON parsing.
    if _MF_MASTER is None:
        path = STATIC_DIR / "mf_master.json"
        if not path.exists():
            raise FileNotFoundError(f"mf_master.json not found at {path}")
                
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
            _MF_MASTER = raw.get("funds", {})
    return _MF_MASTER

# -------------------------------------------------
# STOCK LINKS (INDIA)
# -------------------------------------------------
def stock_links(symbol: str, exchange: str = "NSE") -> Dict[str, dict]:
    """
    Build external research links for Indian stocks.

    The stock ticker is the canonical identifier. URL patterns and
    presentation metadata come from config.json.
    """
    symbol = symbol.upper()

    # URL templates come from the configuration initialized at application startup.
    templates = _EXTERNAL_LINK_CONFIG.get("stocks", {})

    links: Dict[str, dict] = {}

    for provider, template in templates.items():
        #Replace the placeholders for symbol and exchange in the URL template
        url = template["url"].format(symbol=symbol, exchange=exchange)

        # Update the links dictionary with the provider - label, url, and icon
        links[provider] = {
            "label": template["label"],
            "url": url,
            "icon": template["icon"],
        }

    return links

# -------------------------------------------------
# MUTUAL FUND LINKS (INDIA)
# -------------------------------------------------
def mf_links(fund_name: str) -> Dict[str, str]:
    """
    Build external research links for a mutual fund.

    The fund name is resolved through mf_master.json. The master file supplies provider-specific
    identifiers; config.json supplies the corresponding URL templates.

    A provider is included only when its required identifier exists. This preserves the 
    existing "never guess a deep link" behaviour.      
    """
    master = load_mf_master()
    entry = master.get(fund_name)

    if not entry:
      return {}  # No links if the fund name is not found in mf_master.json

    # URL templates come from the configuration initialized at application startup.
    templates = _EXTERNAL_LINK_CONFIG.get("mutual_funds", {})
    
    links: Dict[str, str] = {}

    # Value Research
    vr_id = entry.get("valueresearch_id")
    vr_slug = entry.get("valueresearch_slug")

    if vr_id and vr_slug:
        template = templates.get("valueresearch")
        if template:
            links["valueresearch"] = {
                "label": template["label"],
                "url": template["url"].format(
                    id=vr_id,
                    slug=vr_slug,
                ),
                "icon": template["icon"],
            }

    # Morningstar (only if you filled SecID)
    secid = entry.get("morningstar_secid")
    secslug = entry.get("morningstar_slug")
    if secid and secslug:
        template = templates.get("morningstar")
        if template:
            links["morningstar"] = {
                "label": template["label"],
                "url": template["url"].format(
                    secid=secid,
                    slug=secslug,
                ),
                "icon": template["icon"],
            }

    # Google Finance (only if you normalized name explicitly)
    gf_id = entry.get("google_finance_id")
    if gf_id:
        template = templates.get("googlefinance")
        if template:
            links["googlefinance"] = {
                "label": template["label"],
                "url": template["url"].format(
                    id=gf_id,
                ),
                "icon": template["icon"],
            }

    return links

def security_links(security: str, asset_type: str) -> Dict[str, dict]:
    """
    Return the external research links appropriate for a security.
    Works like a dispathcher to the appropriate link generator based on asset type.

    Supported asset types:
        Stock      -> Google Finance, Yahoo Finance, NSE
        MF(Debt)   -> Value Research, Morningstar, Google Finance
        MF(Equity) -> Value Research, Morningstar, Google Finance

    Other asset types intentionally receive no external links.
    """
    # The asset_type is expected to be a string like "STOCK", "MF-EQUITY", "MF-DEBT", etc.
    # Instead of checking for exact matches, we check if it starts with "MF" to cover both equity and debt mutual funds.
    if asset_type.startswith("MF"):
        return mf_links(security)
    else:
        return stock_links(security)

    return {}
