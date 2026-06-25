# SQLSense - AI-Powered CLI SQL Query Generator

A Python CLI tool that connects to a MySQL database, builds a semantic knowledge base, and converts plain-English questions into safe, read-only SQL `SELECT` queries using deterministic SQL generation with optional AI semantic enrichment during knowledge base build.

**This project is CLI-only.** Run it with `python main.py`.
**AI/LLM is used only during KB build/semantic enrichment.** Runtime question answering is entirely deterministic without AI.

---

## Project Goal

Eliminate the need to write SQL manually. Describe what you want in plain English, let the tool figure out the tables, joins, and filters, validate the SQL for safety, execute it, and show the results in a clean table.

---

## Folder Layout

```
SQL-Sense/
├── main.py                          # CLI entry point, menu loop
├── .env                             # Your credentials (not committed)
├── .env.template                    # Template — copy this to .env
├── requirements.txt                 # Pinned dependencies
├── README.md
├── core/
│   ├── app_service.py               # Main application service orchestrator
│   └── ai_backend_service.py        # AI backend service (KB build only)
├── kb_pipeline/
│   ├── database_service.py         # Database connection and KB build orchestration
│   ├── connection.py                # Engine factory (env + interactive)
│   ├── schema_reader.py             # SQLAlchemy reflection → schema dict
│   ├── data_profiler.py             # Aggregate profiling queries
│   ├── knowledge_base_builder.py    # Orchestrates the KB build
│   ├── schema_facts.py              # Schema enrichment and relationship detection
│   ├── business_glossary.py         # Business term → column mapping
│   ├── ai_semantic_enricher.py      # AI-powered semantic enrichment (KB build only)
│   └── vector/                      # ChromaDB vector store and retrieval
│       ├── chroma_store.py
│       ├── index_builder.py
│       ├── persistence.py
│       └── retriever.py
├── query_pipeline/
│   ├── query_pipeline.py            # Query planning pipeline entry point
│   ├── query_planner.py             # Query planning and routing logic
│   ├── intent_builder.py            # Intent detection (deterministic)
│   ├── context_retriever.py         # Context retrieval from KB/glossary/vector
│   ├── question_normalizer.py       # Question normalization
│   └── conversation/
│       ├── question_rewriter.py     # Follow-up question rewriting (rule-based)
│       └── conversation_memory.py  # Conversation session management
├── sql_pipeline/
│   ├── question_service.py          # Question processing orchestration
│   ├── simple_query_generator.py    # Deterministic SQL for simple queries
│   ├── sql_generator.py             # Blocked AI SQL generator (RuntimeError)
│   ├── sql_validator.py             # SQL validation
│   └── query_executor.py            # Safe SELECT execution
├── core/
│   ├── result_service.py            # Result storage and retrieval
│   ├── chart_service.py             # Chart generation
│   └── insight_service.py          # Insight generation (not yet implemented)
├── utils/
│   ├── file_utils.py                # save_json / load_json
│   └── logger.py                   # Centralized logging configuration
├── semantic/
│   ├── knowledge_base.json          # Generated output (git-ignored)
│   └── business_glossary.json       # Generated business glossary (git-ignored)
├── logs/
│   └── app.log                      # Application logs (git-ignored)
├── output/
│   ├── charts/                      # Generated charts (git-ignored)
│   ├── history/                     # Query history JSON files (git-ignored)
│   └── conversations/               # Conversation session JSON files (git-ignored)
└── tests/                           # Unit and property-based tests
```

---

## Setup Steps

**Requirements:** Python 3.10+ and pip.

```bash
# 1. Clone the project
git clone <repo-url>
cd aisqlqurrey

# 2. Create a virtual environment
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy and fill in the .env template
copy .env.template .env
```

### .env Variables

Open `.env` and set the values that apply to you:

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `DB_HOST` | Yes | — | MySQL server hostname or IP |
| `DB_USER` | Yes | — | MySQL username |
| `DB_PASSWORD` | Yes | — | MySQL password |
| `DB_NAME` | Yes | — | Target database name |
| `LLM_BACKEND` | No | `local` | Active backend is local Ollama |
| `LOCAL_MODEL` | No | `llama3` | Ollama model to use |
| `LOCAL_API_URL` | No | `http://localhost:11434` | Ollama API URL |
| `LOCAL_TIMEOUT` | No | `120` | Local AI timeout in seconds |
| `ENABLE_AI_INSIGHTS` | No | `true` | Enable AI-powered insights after query execution |
| `DEBUG_MODE` | No | `false` | Enable verbose debug logging to logs/app.log |

> `.env` values are only used as a **fallback**. You can also connect from the CLI menu without touching `.env`.

### Default Local AI Configuration

The project is configured to use local Ollama by default:

```env
LLM_BACKEND=local
LOCAL_MODEL=llama3
LOCAL_API_URL=http://localhost:11434
LOCAL_TIMEOUT=120
```

### Setting Up Ollama (local backend)

```bash
# Install from https://ollama.com, then:
ollama pull llama3
ollama serve          # keep this running in a separate terminal
```

The CLI checks `http://localhost:11434/api/tags` before AI enrichment. If Ollama is not running, the tool prints a clean fallback message and continues with rule-based enrichment.

---

## How to Run

```bash
python main.py
```

You will see:

```
====================================================
  AI SQL Query Generator
====================================================
  Backend  : local (llama3)
  Database : not connected
----------------------------------------------------
  1) Connect Database
  2) Build Knowledge Base
  3) Ask a Question / Ask Business Question
  4) Execute Last SQL
  5) AI Backend Settings
  6) Search Business Glossary
  7) Exit
====================================================
```

---

## How to Connect a Database from the CLI

Select **option 1**. The tool will ask for:

```
  Supported database type: mysql
  Database type [mysql]:
  Host [localhost]:
  Port [3306]:
  Username: root
  Database name: mydb
  Password:           ← hidden input, never stored
```

A `SELECT 1` test is run immediately. If it passes, the connection is stored for the session. You will see the database name in the header on every menu.

---

## How to Build the Knowledge Base

Select **option 2**. The tool will:

1. Extract all table names, column names, types, nullable flags, primary keys, and foreign keys.
2. Profile every table: row count, null/non-null/unique counts per column, up to 5 sample values, min/max for numeric and date columns.
3. Assign a semantic type to each column (e.g. `price` → `value`, `customer_id` → `customer`).
4. Optionally enrich with AI using local Ollama `llama3`.
5. Generate a business glossary mapping business terms to actual columns.
6. Save everything to `semantic/knowledge_base.json` and `semantic/business_glossary.json`.

Progress messages:
```
  Run AI semantic enrichment? (y/n): y
  Building knowledge base...
  [OK] Schema extracted successfully.
  [OK] Data profiling completed successfully.
  [OK] Semantic mapping completed successfully.
  [AI] Enriching table: customers
  [OK] AI enrichment completed for table: customers
  ...
  [OK] AI enrichment completed successfully
  [OK] Knowledge base saved successfully -> semantic/knowledge_base.json
  [OK] Business glossary saved -> semantic/business_glossary.json
  Returning to main menu.
```

---

## How to Ask Questions

Select **option 3**. Type a plain-English question:

```
  Enter your question: Show me the top 5 customers by total order value
```

The tool will:
- Load the knowledge base for context
- Use deterministic SQL generation based on the query planner and business glossary
- Validate the generated SQL for safety
- Store and display it if safe

**Note:** Complex queries requiring joins, aggregations, or business reasoning that cannot be handled deterministically will return a clean message: "Complex deterministic SQL generation is not implemented yet. Please try a simpler query."

```
  Generated SQL:
  SELECT c.name, SUM(o.total_amount) AS total
  FROM customers c
  JOIN orders o ON c.id = o.customer_id
  GROUP BY c.id
  ORDER BY total DESC
  LIMIT 5
```

Then select **option 4** to execute the same saved SQL.

---

## AI Semantic Enrichment

When building the knowledge base, you can optionally run AI semantic enrichment. This uses local Ollama `llama3` to add business meaning to tables and columns.

The enrichment flow is designed to finish cleanly:

- Checks Ollama health before enrichment
- Uses `POST /api/chat` with `stream=false`
- Enriches one table at a time
- Uses small column batches instead of one huge prompt
- Falls back only for the table that fails
- Always saves `knowledge_base.json` and `business_glossary.json`
- Always returns to the main menu

If Ollama is not running, times out, or returns invalid JSON, the CLI continues with rule-based enrichment and prints a clean message such as:

- `Ollama is not running. Using rule-based enrichment.`
- `Local AI timed out. Using rule-based fallback.`

- **Table enrichment**: Adds `business_description`, `business_purpose`, and `possible_business_questions`
- **Column enrichment**: Adds `business_description`, `business_terms`, `metric_type`, `is_measure`, `is_dimension`, `is_date`

Example enriched column:
```json
{
  "name": "final_amount",
  "business_description": "Final payable order amount after discounts and taxes.",
  "business_terms": ["sales", "revenue", "order value", "total sales"],
  "metric_type": "currency",
  "is_measure": true,
  "is_dimension": false,
  "is_date": false
}
```

If AI enrichment fails or is skipped, the system falls back to rule-based semantic mapping.

---

## Business Glossary

The business glossary maps plain-English business terms to actual database tables and columns. It's automatically generated when you build the knowledge base.

**Location**: `semantic/business_glossary.json`

**Features**:
- Maps terms like "sales", "revenue", "customer" to specific columns
- Provides example questions for each term
- Includes confidence scores for mappings
- Used by both simple and AI SQL generation

**Example**:
```json
{
  "sales": {
    "description": "Total revenue or order amount.",
    "mapped_columns": [
      {
        "table": "orders",
        "column": "final_amount",
        "confidence": "high"
      }
    ],
    "example_questions": [
      "Show total sales",
      "Show monthly sales",
      "Show sales by city"
    ]
  }
}
```

### Search Business Glossary

Select **option 6** to search the business glossary:

```
  Enter search term (or 'back' to return): sales
  
  Found 1 match(es) for 'sales':
  ----------------------------------------------------
  
  Term: sales
  Description: Total revenue or order amount.
  Mapped columns:
    • orders.final_amount (confidence: high)
  Example questions:
    • Show total sales
    • Show monthly sales
  ----------------------------------------------------
```

---

## Conversation Memory & Follow-up Questions

The tool supports conversational features that remember your previous questions and allow follow-up questions using rule-based rewriting (no AI at runtime).

### Features

- **Follow-up Detection**: Automatically detects when your question is a follow-up (e.g., "Where do they live?", "Make it top 10")
- **Question Rewriting**: Rewrites follow-up questions into standalone questions using rule-based logic
- **Conversation Actions**: Supports commands like "chart", "new chat", "show history"
- **Session Persistence**: Saves conversation sessions to `output/conversations/`

### Follow-up Question Examples

```bash
# First question
  Enter your question: Show all customers
  Generated SQL: SELECT * FROM customers LIMIT 50

# Follow-up question
  Enter your question: Where do they live?
  Follow-up detected.
  Rewritten question: Show customer addresses.
  Generated SQL: SELECT name, city FROM customers LIMIT 50
```

### Conversation Actions

You can use these commands at any time:

- **"chart"** or **"generate chart"**: Generate a chart for the last result
- **"new chat"** or **"clear chat"**: Start a new conversation session
- **"show last sql"** or **"repeat last sql"**: Show the last generated SQL
- **"show history"** or **"show conversation history"**: Show recent conversation turns

### Conversation Sessions

Conversation sessions are automatically saved to `output/conversations/session_YYYYMMDD_HHMMSS.json`. Each session includes:

- Session ID and timestamps
- All conversation turns with:
  - User question
  - Whether it was a follow-up
  - Rewritten question (if applicable)
  - Generated SQL
  - Row count
  - Chart path (if generated)

---

## Example Questions

**Supported (simple list/count):**
```
Show all customers
Count total orders
Show all partners
Count partners
Show all bills
Count bill
```

**Not yet supported (complex queries):**
```
Show total sales by city
Show top 5 customers by sales
Show sales by product category
Show payment details with customer names
Show customers with pending payments
```

Complex queries will return: "Complex deterministic SQL generation is not implemented yet. Please try a simpler query."

---

## Safety Rules

The tool enforces these rules before any SQL touches the database:

| Rule | Detail |
|---|---|
| SELECT only | Queries must start with `SELECT` |
| Blocked keywords | `DROP`, `DELETE`, `UPDATE`, `INSERT`, `ALTER`, `TRUNCATE`, `CREATE`, `REPLACE` — blocked as whole words |
| No multiple statements | Semicolons mid-query are rejected |
| Auto LIMIT | `LIMIT 50` is added automatically if not specified |
| No tracebacks | All errors are shown as a single readable line |
| Password safety | Passwords use `getpass` and are never written to any file |

Example blocked request:

```text
delete all customers
```

This is rejected before SQL execution.

---

## Architecture

### SQL Generation Strategy

The tool uses a **deterministic approach** for SQL generation:

| Query type | Handler | Examples |
|---|---|---|
| Simple list/count | Deterministic rule-based generator | "Show all customers", "Count total orders", "Show all partners" |
| Complex | Not yet implemented (returns clean message) | "Show top 5 customers by total sales", "Show monthly sales", "Revenue by category" |

**Why?** Deterministic SQL generation is safer, faster, and more predictable. AI/LLM is used only during knowledge base build for semantic enrichment, not at runtime for SQL generation. Complex queries requiring joins, aggregations, or business reasoning will return a clean message asking for a simpler query.

### Security

> Do not commit `.env`. It contains real database credentials.
> Use `.env.template` as a reference and fill in your own values in `.env`.

### Database Support

| Database | Status |
|---|---|
| MySQL | Fully supported |
| PostgreSQL | Planned |
| SQLite | Planned |

### Chart Output

Charts are automatically saved with timestamped filenames to `output/charts/chart_YYYYMMDD_HHMMSS.png`. This folder is git-ignored. The tool detects the best chart type (bar, line, or grouped bar) based on the query result structure and asks for confirmation before generating.

### Query History

Every executed query is automatically saved to `output/history/query_YYYYMMDD_HHMMSS.json`. This includes:
- Timestamp
- User question
- Generated SQL
- Row count
- Result rows
- Chart path (if generated)
- Generated insights

The history folder is git-ignored.

### Logging

The application logs all key operations to `logs/app.log`:
- App startup
- Menu choices
- Database connections
- Knowledge base generation
- SQL generation
- Query execution
- Chart generation
- Insight generation
- Errors and AI fallback decisions

Set `DEBUG_MODE=true` in `.env` for verbose debug logging. Passwords and API keys are never logged.

---

## Future Improvements

- Deterministic complex SQL generation (joins, aggregations, business reasoning)
- PostgreSQL support
- SQLite support
- Schema diffing between saved knowledge base and live database
- Query history search and replay
- More deterministic business-question coverage
- Additional AI enrichment improvements for larger schemas
