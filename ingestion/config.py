from pathlib import Path
import os
from typing import Any, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from ingestion.json_io import read_json

# ============================================================
# FinMan Configuration
# ============================================================
#
# Configuration is loaded in two stages:
#
#     config.json
#          +
#     config.<environment>.json
#          ↓
#     merged configuration
#          ↓
#     Pydantic validation
#
# Configuration keys beginning with "_" are ignored.
# ============================================================

# ------------------------------------------------------------
# Frontend
# ------------------------------------------------------------
class FrontendConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")  #forbids extra fields not defined in the model

    owners: dict[str, list[str]]
    default_owner: str
    asset_types: dict[str, list[str]]
    default_asset_type: str

# ------------------------------------------------------------
# Authentication
# ------------------------------------------------------------
class AuthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    password: str
    secret_key: str

# ------------------------------------------------------------
# Server
# ------------------------------------------------------------
class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str
    port: int
    debug: bool
    log_level: str
    log_file: str
    cache_dir: str
    tray_icon: str

# ------------------------------------------------------------
# Templates
# ------------------------------------------------------------
class TemplateNamesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login_template: str
    portfolio_template: str
    broker_template: str
    route_template: str
    holdings_template: str
    route_template: str
    ai_explain_template: str
    status_template: str
    tax_template: str
    tax_broker_template: str
    tax_holding_template: str
    settings_template: str
    research_template: str

# ------------------------------------------------------------
# AI
# ------------------------------------------------------------
class AIProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    priority: int = Field(ge=1, le=7)

    type: str
    model: str
    api_key: str
    timeout_seconds: int | None = None
    max_tokens: int | None = None
    base_url: str | None = None
    supports_json_mode: bool | None = None
    supports_citations: bool | None = None

class AIAnalysisConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    goal: str
    questions: list[dict[str, str]]
    rules: list[str]

class AIConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    top_n: int = Field(default=3, ge=1, le=7)
    timeout_seconds: int
    max_tokens: int
    providers: dict[str, AIProviderConfig]
    analysis: AIAnalysisConfig

# ------------------------------------------------------------
# Cache
# ------------------------------------------------------------
class CacheConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transactions_file: str
    tax_file: str
    mf_master: str
    research: str
    price_snapshot: str

# ============================================================
# Excel / Dataset Configuration
# ============================================================

# ------------------------------------------------------------
# Excel field mapping
# ------------------------------------------------------------
class ExcelFieldMapping(BaseModel):
    """
    Read a value from an Excel column and convert it to the
    specified FinMan type.
    """

    model_config = ConfigDict(extra="forbid")

    header: str
    type: Literal["text", "float", "date"]
    blank_as_zero: bool = False

# ------------------------------------------------------------
# Transformation mapping
# ------------------------------------------------------------
class TransformMapping(BaseModel):
    """
    Derive a normalized field from another source field.
    """

    model_config = ConfigDict(extra="forbid")

    source: str
    transform: dict[str, Any]

# ------------------------------------------------------------
# Mapping value
# ------------------------------------------------------------
#
# A normalized field can be:
#
#   {"header": "...", "type": "..."}   → Excel field
#   {"source": "...", ...}              → transformation
#   "SOMETHING"                         → literal value
#   "derived"                           → populated later
#   null                                → no value
#
# A normal string is deliberately allowed because literals such
# as "MF", "ZERODHA", "FIDELITY", "RSN", "SMALLCASE", etc. are
# part of the configuration.
#
MappingValue = (
    ExcelFieldMapping
    | TransformMapping
    | str
    | None
)

# ------------------------------------------------------------
# Row filter
# ------------------------------------------------------------
class RowFilterCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    op: str
    value: Any | None = None

class RowFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conditions: Annotated[
        list[RowFilterCondition],
        Field(alias="and")
    ]

# ------------------------------------------------------------
# Transaction mappings
# ------------------------------------------------------------
class TransactionMapping(BaseModel):
    """
    Defines the Excel source and field mappings used to create normalized transaction records.
    
    excel_sheet and header_row identify the worksheet and logical header row for transaction ingestion.
    """
    model_config = ConfigDict(extra="forbid")

    excel_sheet: str
    header_row: int

    security: MappingValue
    transaction_date: MappingValue
    transaction_action: MappingValue
    transaction_quantity: MappingValue
    transaction_price: MappingValue
    transaction_value: MappingValue
    position_closed: MappingValue
    owner: MappingValue
    asset_type: MappingValue
    asset_subtype: MappingValue
    broker: MappingValue
    buy_date: MappingValue
    buy_price: MappingValue
    current_price: MappingValue
    current_value: MappingValue

    exchange_rate: MappingValue | None = None
    transaction_price_usd: MappingValue | None = None
    transaction_value_usd: MappingValue | None = None


# ------------------------------------------------------------
# Tax mappings
# ------------------------------------------------------------
class TaxMapping(BaseModel):
    """
    Defines the Excel source and field mappings used to create normalized tax records.

    excel_sheet and header_row identify the worksheet and logical header row for tax ingestion.
    """    
    model_config = ConfigDict(extra="forbid")

    excel_sheet: str
    header_row: int

    fy: MappingValue
    security: MappingValue
    owner: MappingValue
    asset_type: MappingValue
    broker: MappingValue
    sale_date: MappingValue
    buy_price: MappingValue
    quantity: MappingValue
    first_purchase_date: MappingValue
    holding_period: MappingValue
    gain_type: MappingValue
    buy_value: MappingValue
    sell_value: MappingValue
    capital_gain: MappingValue
    tax_rate: MappingValue
    tax: MappingValue

    exchange_rate: MappingValue | None = None
    buy_value_usd: MappingValue | None = None
    sell_value_usd: MappingValue | None = None
    capital_gain_usd: MappingValue | None = None

# ------------------------------------------------------------
# Dataset
# ------------------------------------------------------------
class DatasetConfig(BaseModel):
    """
    Configuration for one Excel source.

    The parent dataset defines the transaction sheet.
    The tax section defines its own sheet and header row.

    The workbook is shared by the configured ingestion mappings.
    Each mapping defines its own worksheet and logical header row.
    """

    model_config = ConfigDict(extra="forbid")

    excel_file: str

    row_filter: RowFilter | None = None

    transactions: TransactionMapping
    tax: TaxMapping

# ------------------------------------------------------------
# Dataset collection
# ------------------------------------------------------------

class DatasetsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stockdata: DatasetConfig
    mfdata: DatasetConfig
    smallcasedata: DatasetConfig
    msftdata: DatasetConfig

# ------------------------------------------------------------
# External research links
# ------------------------------------------------------------
class ExternalLinkConfig(BaseModel):
    """
    Configuration for one external research provider.

    The URL is a template containing placeholders such as
    {symbol}, {exchange}, {id}, {slug}, or {secid}.
    """
    model_config = ConfigDict(extra="forbid")

    label: str
    url: str
    icon: str


class ExternalLinksConfig(BaseModel):
    """
    URL templates and presentation metadata for external
    security research links.
    """
    model_config = ConfigDict(extra="forbid")

    stocks: dict[str, ExternalLinkConfig]
    mutual_funds: dict[str, ExternalLinkConfig]

# ============================================================
# Root configuration
# ============================================================

class FinManConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frontend: FrontendConfig
    auth: AuthConfig
    server: ServerConfig
    template_names: TemplateNamesConfig
    ai: AIConfig
    cache: CacheConfig
    datasets: DatasetsConfig

    # URL templates and metadata for external security research links.
    external_links: ExternalLinksConfig

# ============================================================
# Internal configuration functions
# ============================================================
def _merge_config(base: dict, override: dict) -> dict:
    """
    Recursively merge an environment-specific configuration into the base configuration.

    Dictionary values are merged recursively; other values from the override replace the base value.
    Configuration keys beginning with _ are ignored.

    Returns: Merged configuration dictionary.    
    """
    result = base.copy()

    for key, value in override.items():
        if key.startswith("_"):  # Ignore keys starting with "_"
            continue

        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _merge_config(result[key], value)
        else:
            result[key] = value

    return result

def _resolve_config_paths(config: dict, base_dir: Path) -> dict:
    """
    Resolve configured file and directory paths relative to the FinMan base directory or user environment.
    Internal helper: called by load_config().
    """
    for dataset in config["datasets"].values():
        dataset["excel_file"] = _resolve_path(dataset["excel_file"], base_dir)

    config["server"]["log_file"] = _resolve_path(config["server"]["log_file"], base_dir)
    config["server"]["cache_dir"] = _resolve_path(config["server"]["cache_dir"], base_dir)

    return config

def _resolve_path(value: str, base_dir: Path) -> str:
    """
    Resolve a single configured path, including supported environment/user-home placeholders and relative paths.
    Internal helper: called by _resolve_config_paths().
    Supported forms:
        ${USER_HOME}
        %USERPROFILE%
        ~
    Non-string values are returned unchanged.
    """

    if not isinstance(value, str):
        return value

    value = os.path.expandvars(value)

    if "${USER_HOME}" in value:
        value = value.replace("${USER_HOME}", str(Path.home()))

    value = os.path.expanduser(value)
    path = Path(value)

    if not path.is_absolute():
        path = base_dir / path

    return str(path)

def get_config_environment():
    return os.getenv("FINMAN_CONFIG", "dev").lower()

# ============================================================
# Configuration Loading
# ============================================================
def load_config(base_dir: Path) -> FinManConfig:
    """
    Load FinMan's base and environment-specific configuration, resolve configured paths,
    and validate the final configuration with Pydantic.

    The FINMAN_CONFIG environment variable selects the environment; dev is the default.

    Returns: Validated FinManConfig.
    Example:
        FINMAN_CONFIG=dev
    loads:
        config.json
        config.dev (or prod).json
    """
    config = read_json(base_dir / "config.json")

    environment = get_config_environment()

    override_file = base_dir / f"config.{environment}.json"

    if override_file.exists():
        override = read_json(override_file)
        config = _merge_config(config, override)

    config = _resolve_config_paths(config, base_dir)

    return FinManConfig.model_validate(config)

