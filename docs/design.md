                       config.json
                           │
                           │ model_validate()
                           ▼
                    ┌───────────────┐
                    │ FinManConfig  │
                    └───────┬───────┘
                            │
                       datasets.mfdata
                            │
                            ▼
                    ┌───────────────┐
                    │ DatasetConfig │
                    └───────┬───────┘
                            │
              ┌─────────────┴─────────────┐
              │                           │
              ▼                           ▼
        RowFilter                 TransactionMapping
              │                           │
              ▼                           ├── security
     RowFilterCondition                  │      ↓
     RowFilterCondition                  │ ExcelFieldMapping
     RowFilterCondition                  │
                                          ├── quantity
                                          │      ↓
                                          │ ExcelFieldMapping
                                          │
                                          ├── broker
                                          │      ↓
                                          │ "MF"
                                          │
                                          └── current_price
                                                 ↓
                                             "derived"


                 EXCEL
                   │
                   ▼
             raw dictionary
                   │
                   │ map_transaction_row()
                   ▼
          normalized dictionary
                   │
                   │ apply_row_filter()
                   ▼
              accepted?
               /      \
             NO        YES
             │          │
          discard       │
                        │
                        ▼
             Transaction.model_validate()
                        │
                        ▼
                  Transaction
                        │
                        │ model_dump()
                        ▼
                JSON transaction


| Piece                           | What it really is           | Job                                              |
| ------------------------------- | --------------------------- | ------------------------------------------------ |
| `BaseModel`                     | Pydantic base class         | Gives a class Pydantic capabilities              |
| `ConfigDict`                    | Pydantic model instructions | Controls model behavior such as `extra="forbid"` |
| `ExcelFieldMapping`             | Pydantic model              | Describes "get this field from Excel"            |
| `TransformMapping`              | Pydantic model              | Describes "derive this field from another value" |
| `MappingValue`                  | Python type alias           | Says what kinds of mappings are permitted        |
| `RowFilterCondition`            | Pydantic model              | Describes one filtering rule                     |
| `RowFilter`                     | Pydantic model              | Holds the conditions                             |
| `TransactionMapping`            | Pydantic model              | Holds mappings for all Transaction fields        |
| `DatasetConfig`                 | Pydantic model              | Holds everything needed for one dataset          |
| `FinManConfig`                  | Pydantic model              | Holds the entire application configuration       |
| `Transaction`                   | Pydantic model              | Defines the normalized transaction contract      |
| `map_value()`                   | Python function             | Interprets one mapping                           |
| `apply_row_filter()`            | Python function             | Actually executes filtering logic                |
| `read_excel_rows()`             | Python function             | Reads Excel without interpreting it              |
| `ingest_dataset_transactions()` | Python function             | Orchestrates one dataset                         |
| `ingest_transactions()`         | Python function             | Orchestrates all datasets                        |


Known limitations / Future enhancements
===============================================
Future enhancement — price date: updated_at currently records when the price refresh completed, not the date of the market price itself. Yahoo may return the most recent valid closing price when the latest row is unavailable (e.g. today's close is still NaN). At some point, the price snapshot should capture the actual date associated with each price (price_date) so that the application can distinguish a current price from an older/stale market price.
refresh timestamp
    = when FinMan obtained/saved the price
price date
    = date to which the market price actually belongs


Big PICTURE of the HOLDINGS Computation
=================================================

                    TRANSACTIONS
                         │
                         │ filter
                         ▼
                  ┌─────────────┐
                  │   GROUPING  │
                  │ security +  │
                  │ owner +     │
                  │ type +      │
                  │ broker      │
                  └──────┬──────┘
                         │
                         ▼
                     HOLDING
                         │
             ┌───────────┼────────────┐
             │           │            │
             ▼           ▼            ▼
          Current       Total       Closed
             │           │            │
             └───────────┼────────────┘
                         │
                         ▼
                 PORTFOLIO SUMMARY
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
        PortfolioView           BrokerView
              │                     │
         group by type          group by broker
              │                     │
              └──────────┬──────────┘
                         ▼
                       HTML

Transactions are the source data. Holdings are calculated data. Views are presentation-oriented calculated data.

transactions.json
       │
       ▼
┌──────────────────┐
│ build_holdings() │
└──────────────────┘
       │
       ▼
   Holding[]
       │
       ├───────────────┐
       ▼               ▼
PortfolioSummary   HoldingDetail
       │
       ▼
PortfolioView / BrokerView
       │
       ▼
      HTML


BIG PICTURE of the TAX VIEWS Computation
=============================================

                    app.state.tax
                         │
                         │
                 _filter_tax_transactions
                         │
                  FY + Owner data
                         │
             ┌───────────┴───────────┐
             │                       │
             ▼                       ▼
    build_tax_portfolio_view   build_tax_broker_view
             │                       │
      Asset + Owner             Broker + Owner
             │                       │
             ▼                       ▼
   security aggregation       security aggregation
             │                       │
             ▼                       ▼
       Tax Portfolio             Tax Broker
             │                       │
             └───────────┬───────────┘
                         │
                      click row
                         │
                         ▼
                 build_tax_detail
                         │
                         ▼
                Individual transactions

and the _buid_tax_summary:

Tax transactions
       │
       └──→ _build_tax_summary()
                 ↓
           LT / ST totals



Settings System
==================================================
There are 3 layers:

                    BROWSER
                       │
                       ▼
              ┌─────────────────┐
              │    finman.py    │
              │                 │
              │ HTTP routes     │
              │ request/response│
              └────────┬────────┘
                       │
                       ▼
              ┌─────────────────┐
              │   settings.py   │
              │                 │
              │ Settings logic  │
              │ JSON handling   │
              │ validation      │
              │ masking         │
              └────────┬────────┘
                       │
             ┌─────────┴─────────┐
             ▼                   ▼
       JSON files          Pydantic Config
 config.json etc.       FinManConfig / app.state

 and then the HTML sits alongside:

 finman.py
   │
   ├── /settings
   │       │
   │       └── settings.html
   │
   ├── /settings/edit/...
   │       │
   │       └── settings_editor.html
   │
   └── /settings/edit/... POST


SETTINGS_FILES
==============
SETTINGS_FILES is the heart of the design. This abstracts the files to be managed (config.json, config_dev, etc)
Additionally it also abstracts what sections are editable, if the file necessaites a config validtion and 
if a config reload is needed.
The constants:
SETTINGS_EXPAND_KEYS - controls the navigation


Formatting the config.json back after saving
=================================================
funciton - _format_config_json(data)

START
  |
  v
Take the entire config
  |
  v
Is this a simple value?
  |---- YES ---> write it normally
  |
  NO
  |
  v
Would the whole thing fit in <= 100 characters?
  |---- YES ---> put it on one line
  |
  NO
  |
  v
Is it a list?
  |---- YES ---> format each item recursively
  |
  NO
  |
  v
It's a dictionary
  |
  v
Write {
  |
  v
Format each value recursively
  |
  v
Write }
  |
  v
Return formatted text


The entire SAVE process in one picture (settings -> save)
============================================================
USER
 │
 │ clicks Edit
 ▼
GET /settings/edit/config/frontend
 │
 ▼
settings_edit()
 │
 ├── get_settings_file()
 ├── read_settings_file()
 ├── get_settings_section()
 ├── mask_secrets()
 │
 ▼
settings_editor.html
 │
 │ user changes JSON
 │
 │ clicks Save
 ▼
POST /settings/edit/config/frontend
 │
 ▼
save_settings_edit()
 │
 ├── read HTTP body
 ├── parse form
 ├── json.loads()
 │
 ▼
section_data
 │
 ▼
save_settings_section()
 │
 ├── check editable
 │
 ├── current config
 │       │
 │       ▼
 │   model_dump()
 │       │
 │       ▼
 │   proposed_config
 │       │
 │       ├── replace edited section
 │       │
 │       ▼
 │   FinManConfig.model_validate()
 │
 ├── read physical JSON file
 │
 ├── replace section
 │
 ├── write config.tmp
 │
 ├── replace config.json
 │
 └── load_config()
          │
          ▼
   NEW FinManConfig
          │
          ▼
request.app.state.config
          │
          ▼
configure_external_links()
          │
          ▼
      303 redirect
          │
          ▼
GET /settings


Understading the settings_editor.html file
============================================
                    CONFIG FILE
                         │
                         ▼
                  Python reads it
                         │
                         ▼
                   section_data
                         │
                         ▼
               settings_editor.html
                         │
                         ▼
                 render_value()
                         │
                Jinja creates HTML
                         │
                         ▼
                    BROWSER
                         │
              User edits fields
                         │
                         ▼
                 Clicks Save
                         │
                         ▼
                  JavaScript
                         │
               reads every field
                         │
                         ▼
                data-editor-path
                tells it WHERE
                         │
                         +
                data-editor-kind
                tells it WHAT TYPE
                         │
                         ▼
                    setValue()
                         │
                         ▼
                  result = {...}
                         │
                         ▼
                JSON.stringify()
                         │
                         ▼
                hidden "content"
                         │
                         ▼
                  form.submit()
                         │
                         ▼
                    Python POST
                         │
                         ▼
                 json.loads()
                         │
                         ▼
                  section_data
                         │
                         ▼
              save_settings_section()
                         │
                         ▼
                    JSON file


