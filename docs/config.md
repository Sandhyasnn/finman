# FinMan Configuration

## Purpose

`config.json` contains all application configuration and the mapping information required to convert 
the Excel input files into FinMan's normalized JSON datasets.

The configuration is intended to keep source-specific Excel knowledge out of the application code.

The general principle is:
    Excel files
        ↓
    Config-driven import / normalization
        ↓
    transactions.json / tax.json
        ↓
    portfolio calculations and display

The application should not contain separate hardcoded import logic for
Stocks, Mutual Funds, Smallcases, or MSFT.

---

# Configuration conventions

Dataset field mappings use four forms.

## 1. Excel column mapping

Example:

    "quantity": {"header": "Quantity", "type": "float"}

This means:
- Read the Excel column whose header is `Quantity`
- Convert the value to `float`
- Store it in the normalized JSON field `quantity`

Supported types currently used:
- `text`
- `float`
- `date`

The JSON field name is the normalized FinMan name.
The Excel header is the source-specific name.

For example:
    "date": {"header": "Date of Transaction", "type": "date"}

The normalized field is `date`, even though the Excel column is
called `Date of Transaction`.

---

## 2. Hardcoded value

Example:
    "asset_type": "SMALLCASE"

A string value that does not contain a `header`, `source`, or `derived` definition is a literal value.

It is written directly to every normalized record generated from that dataset.

Examples:
    "security": "MSFT"
    "owner": "RSN"
    "broker": "FIDELITY"
    "asset_type": "US STOCK"

This is intentional. These values are properties of the source
dataset rather than values that need to be read from Excel.

---

## 3. Derived field

Example:
    "current_price": "derived"

`derived` means the value is not obtained from the Excel source.

It is populated by a later FinMan processing step.
For transactions:
    current_price
    current_value
are populated after the transaction data has been imported.

The price-refresh process obtains current prices and updates `price_snapshot.json`, then uses 
that snapshot to populate the  current valuation fields in `transactions.json`.

Therefore these fields should NOT have an Excel column mapping.

---

## 4. Transformation

Example:
    "broker": {
        "source": "owner",
        "transform": {"contains": "(z)", "true": "ZERODHA", "false": "CBSL"}}

This means:
1. Read the `owner` value from Excel.
2. Check whether it contains `(z)`.
3. If it does, set `broker` to `ZERODHA`.
4. Otherwise set `broker` to `CBSL`.

Transformations are used where a normalized field can be derived
from another source field without application-specific Python code.

---

# Frontend

## owners

Defines the normalized FinMan owner names and the corresponding
values that may occur in the source data.
Example:
    "RSN": ["RSN"],
    "MALATHI": ["Malt", "Malt(z)", "Usha"],
    "SANDHYA": ["Sandhya"],
    "ALL": ["ALL"]

`default_owner` defines the owner selected when the application
starts.

---

# Authentication
`auth.enabled` controls whether authentication is enabled.

The password and secret key are configuration secrets and should not contain real production 
secrets in a source-controlled configuration file.

Prefer environment variables or another secrets mechanism for real deployments.

---

# Server
The `server` section controls:
- listening host
- port
- debug mode
- logging level
- log file
- cache directory
- application tray icon

These are environment/runtime settings and are independent of the Excel data mappings.

---

# Templates

`template_names` contains the names of the HTML templates used by FinMan.

The application should obtain template names from this section rather than hardcoding template filenames in Python routes.

---

# AI

The `ai` section controls:
- whether AI functionality is enabled
- default providers
- request timeout
- maximum token count
- provider-specific configuration

API keys are secrets and should not be stored as real values in
a shared configuration file.

---

# Cache

The `cache` section defines files generated or maintained by FinMan.

## transactions_file
    transactions.json

Normalized transaction data from all transaction sources.

The four Excel sources are combined into this single logical dataset.

## tax_file
    tax.json

Normalized tax records from all tax sources.

## mf_master
    static/mf_master.json

Mutual-fund master/reference data used by FinMan.

## research
    cache/research/research_index.json

Cached mutual-fund research data.

## price_snapshot
    cache/price_snapshot.json

Latest price snapshot obtained from the configured price sources.

Stocks are obtained from the stock price source currently used
by FinMan, and mutual-fund prices from the MF price source.

`price_snapshot.json` is the authoritative current-price snapshot.

The current price and current value in `transactions.json` are derived/materialized from this snapshot.

---

# Datasets

Each entry under `datasets` represents an Excel input source.

The source-specific configuration contains:
- Excel filename
- Excel sheet
- header row
- optional row filtering
- transaction field mappings
- tax field mappings

The purpose of this section is to isolate all knowledge of the
Excel source formats from the rest of the application.

---

# Normalized transactions

All transaction sources are converted to the same normalized transaction schema.

Core fields:
    security
    date
    action
    quantity
    price
    value
    closed
    owner
    asset_type
    asset_subtype
    broker
    buy_date
    buy_price
    current_price
    current_value

MSFT additionally has:
    exchange_rate
    price_usd
    value_usd

The normalized transaction schema is intentionally independent of the Excel column names.

For example:
    Excel: "Date of Transaction"
    JSON:  "date"

and:
    Excel: "Buy/Sell Price"
    JSON:  "price"

---

# Normalized tax data

All tax sources are converted to the same normalized tax schema.

Fields:
    fy
    security
    owner
    sale_date
    buy_price
    quantity
    first_purchase_date
    holding_period
    gain_type
    buy_value
    sell_value
    capital_gain
    tax_rate
    tax

MSFT additionally has:
    exchange_rate
    buy_value_usd
    sell_value_usd
    capital_gain_usd

---

# Row filters

Some Excel sheets contain rows that are not actual transactions.

A `row_filter` defines which rows are imported.

Example:
    "row_filter": {
        "and": [
            {
                "field": "type1",
                "op": "!=",
                "value": "Summarized"
            },
            {
                "field": "symbol",
                "op": "not_null"
            }
        ]
    }

Only rows satisfying all conditions are imported.

The filtering is performed before the normalized transaction
records are created.

---

# Current price and current value

`current_price` and `current_value` are deliberately marked as:

    "derived"

They are not Excel fields.

The intended process is:

    Excel
      ↓
    transactions.json
      ↓
    obtain current prices
      ↓
    price_snapshot.json
      ↓
    update transactions.json
      ↓
    current_price
    current_value

`current_value` is a materialized value so that portfolio views, summaries, and XIRR cash-flow
construction can use it directly without repeatedly calculating:

    current quantity × current price

The values in `transactions.json` therefore represent the portfolio valuation as of the last price refresh.

---

# Important design principle

FinMan JSON is intentionally denormalized.

It is not intended to reproduce a relational/ER database design.

Some information may therefore be repeated in multiple transaction records when doing so 
makes retrieval and calculation simpler.

The goal is:
    simple data retrieval
    simple calculations
    minimal application code

rather than:
    minimum data duplication

The authoritative source for each type of information should remain clear.

For example:

    Excel transaction history
        → source transaction data

    price_snapshot.json
        → authoritative current prices

    transactions.json current_price/current_value
        → derived/materialized values

---

# Do not add source-specific logic to Python

If a source requires a fixed value, put it in `config.json`.

Example:

    "broker": "FIDELITY"

If a value needs a transformation, describe the transformation in `config.json`.

Only logic that is genuinely application/business logic should be implemented in Python.

This keeps the Excel import process generic.

## Disabled configuration keys

Any configuration key whose name begins with `_` is ignored by the FinMan configuration loader.

For example:
    "_old_field": {
        "header": "Old Column",
        "type": "text"
    }

is retained in the configuration file but is not processed.

The underscore rule applies at every level of the configuration hierarchy.

The `_` prefix applies only to the key name. It has no special meaning when used in a configuration value.

This provides a simple way to temporarily disable configuration without deleting it.


