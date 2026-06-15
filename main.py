"""
main.py
=======
CLI entry point for the AI SQL Query Generator.

Menu flow
---------
  1) Connect Database      — collect credentials, test connection, store engine
  2) Build Knowledge Base  — schema → profile → semantic mapping → save JSON
  3) Ask a Question        — NL question → AI → validated SQL → store in session
  4) Execute Last SQL      — run stored SQL, display results as a table
  5) AI Backend Settings   — view local Ollama/Llama3 status
  6) Search Business Glossary
  7) Exit

All exceptions are caught at the boundary of each handler so the user
never sees a raw Python traceback — only a clean one-line error message.

Phase 5: CLI is now thin - business logic moved to core services.
CLI only handles menu display, input collection, and output formatting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import getpass
import os
import sys

from dotenv import load_dotenv

# Core services - business logic is here
from core.app_service import AppService

# Keep these for CLI-specific utilities
from db.connection import SUPPORTED_DB_TYPES
from utils.logger import get_logger

# Initialize logger
logger = get_logger()

# Default TCP ports for each supported database type.
_DEFAULT_PORTS: dict[str, int] = {
    "mysql": 3306,
    "postgresql": 5432,
}


# ── Session state ─────────────────────────────────────────────────────────────

@dataclass
class SessionState:
    """
    Holds CLI-specific state for the current CLI session.
    
    Business logic is now in AppService (core/app_service.py).
    
    Attributes:
        app_service     The main application service (contains all business logic).
    """
    app_service: AppService = field(default_factory=AppService)


# ── Small helpers ─────────────────────────────────────────────────────────────

def _current_backend_from_env() -> str:
    """Read LLM_BACKEND from .env; fall back to 'local' for unknown values."""
    return "local"


def _backend_label(state: SessionState) -> str:
    """One-line label for the active AI backend shown in the menu header."""
    config = state.app_service.get_backend_config()
    return f"local ({config.get('model', 'llama3')})"


def _db_label(state: SessionState) -> str:
    """One-line label for the active database connection shown in the menu header."""
    if not state.app_service.is_database_connected():
        return "not connected"
    db_config = state.app_service.database_service.get_db_config()
    if not db_config:
        return "not connected"
    db_type = db_config.get("db_type", "unknown")
    if db_type == "sqlite":
        return f"sqlite  →  {db_config.get('sqlite_path', '')}"
    return (
        f"{db_type}  →  "
        f"{db_config.get('username', '')}@{db_config.get('host', '')}/"
        f"{db_config.get('database', '')}"
    )


def _prompt(label: str, default: str = "") -> str:
    """Show a prompt with an optional default; return user input or the default."""
    hint = f" [{default}]" if default else ""
    raw = _input(f"  {label}{hint}: ")
    return raw if raw else default


def _prompt_int(label: str, default: int) -> int:
    """Prompt for an integer; fall back to *default* on empty or invalid input."""
    raw = _input(f"  {label} [{default}]: ")
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"  Invalid number '{raw}' — using default {default}.")
        return default


def _input(prompt: str) -> str:
    """
    Thin wrapper around input() that flushes stdout first.

    On Windows, stdout can buffer content that hasn't been displayed yet,
    causing prompts to appear out of order or after the cursor.
    Flushing before every input() call prevents this and also helps after
    getpass.getpass() which can leave the console in a buffered state.
    """
    sys.stdout.flush()
    return input(prompt).strip()


# ── Menu display ──────────────────────────────────────────────────────────────

def display_menu(state: SessionState) -> None:
    """Print the full CLI menu with current session context in the header."""
    print()
    print("=" * 52)
    print(f"  AI SQL Query Generator")
    print("=" * 52)
    print(f"  Backend  : {_backend_label(state)}")
    print(f"  Database : {_db_label(state)}")
    
    last_sql = state.app_service.get_last_sql()
    if last_sql:
        # Show a short preview of the last SQL so the user knows it's ready.
        preview = last_sql.replace("\n", " ")
        if len(preview) > 48:
            preview = preview[:48] + "…"
        print(f"  Last SQL : {preview}")
    print("-" * 52)
    print("  1) Connect Database")
    print("  2) Build Knowledge Base")
    print("  3) Ask a Question / Ask Business Question")
    print("  4) Execute Last SQL")
    print("  5) AI Backend Settings")
    print("  6) Search Business Glossary")
    print("  7) Exit")
    print("=" * 52)


def read_menu_choice() -> int | None:
    """
    Read a menu choice from the user and return it as an integer (1–7).

    Uses plain input() via _input() — no special key libraries, no raw
    terminal mode.  The user types a digit and presses Enter normally.

    Returns None on empty or invalid input so the caller can loop again.
    """
    raw = _input("  Choose an option (1-7): ")

    if not raw:
        print("  Please enter a menu option.")
        return None

    if raw not in {"1", "2", "3", "4", "5", "6", "7"}:
        print(f"  Invalid option '{raw}'. Please choose 1 to 7.")
        return None

    return int(raw)


# ── Option handlers ───────────────────────────────────────────────────────────

def handle_connect_database(state: SessionState) -> None:
    """
    Phase 1 — Connect Database.
    Collects connection details interactively, tests the connection with
    SELECT 1, and stores the engine in SessionState on success.
    Password is collected via getpass and never written to any file.
    """
    logger.info("User chose option 1: Connect Database")
    print(f"\n  Supported database type: mysql")
    print("  PostgreSQL and SQLite are planned for future phases.\n")

    db_type = _prompt("Database type", "mysql").lower()
    if db_type not in SUPPORTED_DB_TYPES:
        print(f"  Unsupported type '{db_type}'. Only mysql is currently supported.")
        print("  PostgreSQL and SQLite are planned for future phases.")
        return

    # ── SQLite path ──────────────────────────────────────────────────────────
    if db_type == "sqlite":
        sqlite_path = _prompt("SQLite file path")
        if not sqlite_path:
            print("  SQLite file path cannot be empty.")
            return
        success, message, engine = state.app_service.connect_database(
            db_type="sqlite",
            sqlite_path=sqlite_path,
        )
        if not success:
            print(f"  Connection failed: {message}")
            return
        print(f"  Connected to SQLite: {sqlite_path}")
        return

    # ── MySQL / PostgreSQL ───────────────────────────────────────────────────
    host     = _prompt("Host", "localhost")
    port     = _prompt_int("Port", _DEFAULT_PORTS.get(db_type, 3306))
    username = _prompt("Username")
    if not username:
        print("  Username cannot be empty.")
        return
    database = _prompt("Database name")
    if not database:
        print("  Database name cannot be empty.")
        return

    # getpass hides the password so it is never echoed in the terminal.
    # Flush stdout before and after so the Windows console buffer is clean.
    try:
        sys.stdout.flush()
        password = getpass.getpass("  Password: ")
        sys.stdout.flush()  # restore normal stdout state after getpass
    except (KeyboardInterrupt, EOFError):
        print("\n  Password input cancelled.")
        return

    print(f"\n  Connecting to {db_type}://{username}@{host}:{port}/{database} …")
    success, message, engine = state.app_service.connect_database(
        db_type=db_type,
        host=host,
        port=port,
        username=username,
        password=password,
        database=database,
    )
    if not success:
        logger.error(f"Database connection failed: {message}")
        print(f"  Connection failed: {message}")
        return

    logger.info(f"Successfully connected to database: {db_type}://{username}@{host}:{port}/{database}")
    print(f"  Successfully connected to {_db_label(state)}.")


def handle_build_knowledge_base(state: SessionState) -> None:
    """
    Phase 2 — Build Knowledge Base.
    Uses the connected engine to extract schema, profile data, apply
    semantic mapping, and save to semantic/knowledge_base.json.
    Optionally enriches with AI and generates business glossary.
    Prints a progress message after each step.
    Never writes the file if any step fails.
    """
    logger.info("User chose option 2: Build Knowledge Base")
    if not state.app_service.is_database_connected():
        print("  No database connection. Please run option 1 first.")
        return

    # Ask about AI semantic enrichment
    try:
        answer = _input("\n  Run AI semantic enrichment? (y/n): ").lower()
    except (KeyboardInterrupt, EOFError):
        print()
        answer = "n"

    use_ai_enrichment = (answer == "y")
    ai_backend = "local"

    print("\n  Building knowledge base…")
    success, message, knowledge_base = state.app_service.build_knowledge_base(
        use_ai_enrichment=use_ai_enrichment,
        ai_backend=ai_backend,
    )
    if not success:
        logger.error(f"Knowledge base build failed: {message}")
        print(f"  Knowledge base build failed: {message}")
        return

    if use_ai_enrichment:
        enrichment_status, enrichment_message = state.app_service.get_last_ai_enrichment_result()
        if enrichment_status == "completed":
            print("  [OK] AI enrichment completed successfully")
        elif enrichment_status == "partial":
            print(f"  [OK] {enrichment_message}")
        else:
            print(f"  [OK] AI enrichment skipped/fallback used ({enrichment_message})")
    else:
        print("  [OK] AI enrichment skipped/fallback used")

    build_summary = state.app_service.get_last_build_summary()
    if build_summary:
        print("\n  Build Summary:")
        modules_detected = build_summary.get("modules_detected", {})
        if modules_detected:
            module_parts = [f"{module}={count}" for module, count in sorted(modules_detected.items())]
            print(f"  - modules detected: {', '.join(module_parts)}")
        print(f"  - relationships detected: {build_summary.get('relationship_count', 0)}")

        low_confidence_relationships = build_summary.get("low_confidence_relationships", [])
        if low_confidence_relationships:
            print("  - low confidence relationships:")
            for relationship in low_confidence_relationships[:5]:
                print(
                    "    "
                    f"{relationship.get('from_table')}.{relationship.get('from_column')} -> "
                    f"{relationship.get('to_table')}.{relationship.get('to_column')} "
                    f"(confidence: {relationship.get('confidence')}, source: {relationship.get('source')})"
                )
        else:
            print("  - low confidence relationships: none")

        missing_relationship_tables = build_summary.get("tables_with_missing_relationships", [])
        if missing_relationship_tables:
            print(f"  - tables with missing relationships: {', '.join(missing_relationship_tables)}")
        else:
            print("  - tables with missing relationships: none")

    print(f"  [OK] Knowledge base saved successfully -> semantic/knowledge_base.json")
    print(f"  [OK] Business glossary saved -> semantic/business_glossary.json")
    print("  Returning to main menu.")


def handle_ask_question(state: SessionState) -> None:
    """
    Option 3 — Ask a Question (Hybrid SQL Generation with Conversation Memory).

    Flow
    ----
    1. Load the knowledge base.
    2. Receive the user's question.
    3. Check action detector (chart, insights, new_chat, etc.).
    4. If action exists, handle action and return to menu.
    5. Process question using core service.
    6. Display the SQL.
    7. Save conversation session.
    """
    logger.info("User chose option 3: Ask a Question")
    
    # ── Load knowledge base ───────────────────────────────────────────────
    success, message, knowledge_base = state.app_service.load_knowledge_base()
    if not success:
        print(f"  {message}")
        return

    # ── Get the question ──────────────────────────────────────────────────
    question = _input("\n  Enter your question: ")
    if not question:
        logger.warning("Empty question submitted")
        print("  Question cannot be empty.")
        return
    if len(question) > 500:
        logger.warning(f"Question too long: {len(question)} characters")
        print("  Question is too long — please keep it to 500 characters or fewer.")
        return

    logger.info(f"User question: {question}")

    # ── Check for conversation actions ─────────────────────────────────────
    action = state.app_service.detect_action(question)
    if action:
        logger.info(f"Action detected: {action}")
        _handle_conversation_action(action, state)
        return

    # ── Process question using core service ───────────────────────────────
    ai_backend = state.app_service.get_active_backend()
    success, message, sql, error = state.app_service.process_question(question, ai_backend)
    if not success:
        print(f"  {error or message}")
        return

    # ── Display the SQL ───────────────────────────────────────────────────
    query_context = state.app_service.get_last_query_context() or {}
    query_plan = query_context.get("plan") or {}

    if query_plan:
        print("\n  Query Plan:")
        print(f"  - intent: {query_plan.get('intent')}")
        print(f"  - metric: {query_plan.get('metric')}")
        print(f"  - dimension: {query_plan.get('dimension')}")
        print(f"  - filters: {query_plan.get('filters')}")
        print(f"  - date range: {query_plan.get('date_range')}")

    selected_tables = query_context.get("selected_tables", [])
    if selected_tables:
        print("\n  Selected Tables:")
        for table_entry in selected_tables:
            print(f"  - {table_entry.get('table', '')} (confidence: {table_entry.get('confidence', 'unknown')})")
            if table_entry.get("reason"):
                print(f"    reason: {table_entry['reason']}")
            selected_columns = table_entry.get("selected_columns", [])
            if selected_columns:
                column_descriptions = [
                    f"{column_entry.get('column')} [{column_entry.get('semantic_type', 'general')}]"
                    for column_entry in selected_columns[:6]
                ]
                print(f"    selected columns: {', '.join(column_descriptions)}")

    if query_context.get("confidence") is not None:
        print(f"\n  Planning Confidence: {query_context['confidence']}")
    if query_context.get("generation_confidence") is not None:
        print(f"  Generation Confidence: {query_context['generation_confidence']}")

    if query_context.get("warnings"):
        print("\n  Warnings:")
        for warning in query_context["warnings"]:
            print(f"  - {warning}")

    print("\n  Generated SQL:")
    print(f"  {sql}")
    
    # ── Save conversation session ─────────────────────────────────────────
    conversation_memory = state.app_service.get_conversation_memory()
    conversation_memory.save_session()


def _handle_conversation_action(action: str, state: SessionState) -> None:
    """
    Handle conversation actions like chart, insights, new_chat, etc.
    
    Args:
        action: The action to handle
        state: The session state
    """
    logger.info(f"Handling conversation action: {action}")
    
    if action == "chart":
        # Generate chart for last results
        rows = state.app_service.get_last_rows()
        if not rows:
            print("  No results to chart. Please execute a query first.")
            return
        success, message, chart_path, chart_type = state.app_service.generate_chart(rows)
        if success:
            print(f"  Chart saved to: {chart_path}")
        else:
            print(f"  {message}")
    
    elif action == "insights":
        # Generate insights for last results
        rows = state.app_service.get_last_rows()
        sql = state.app_service.get_last_sql()
        if not rows or not sql:
            print("  No results to analyze. Please execute a query first.")
            return
        success, message, insights = state.app_service.generate_insights()
        if success:
            print("\n  Insights:")
            for insight in insights:
                print(f"  • {insight}")
        else:
            print(f"  {message}")
    
    elif action == "new_chat":
        # Start a new conversation
        logger.info("Starting new conversation")
        state.app_service.reset_conversation()
        print("  New conversation started.")
    
    elif action == "repeat_last_sql":
        # Show last SQL
        sql = state.app_service.get_last_sql()
        if not sql:
            print("  No SQL has been generated yet.")
            return
        print(f"\n  Last SQL:")
        print(f"  {sql}")
    
    elif action == "show_history":
        # Show conversation history
        conversation_memory = state.app_service.get_conversation_memory()
        turns = conversation_memory.get_recent_turns(5)
        if not turns:
            print("  No conversation history.")
            return
        print("\n  Recent conversation turns:")
        for turn in turns:
            print(f"\n  Turn {turn['turn_id']}:")
            print(f"  User: {turn['user_question']}")
            if turn['is_follow_up']:
                print(f"  Rewritten: {turn['rewritten_question']}")
            print(f"  SQL: {turn['generated_sql'][:80]}...")
            print(f"  Rows: {turn['row_count']}")
    
    else:
        logger.warning(f"Unknown action: {action}")
        print(f"  Unknown action: {action}")


def _format_table(rows: list[dict]) -> str:
    """
    Format a list of row dicts as a fixed-width CLI table.
    Uses str.ljust() for alignment — no third-party library needed.
    """
    if not rows:
        return ""

    columns = list(rows[0].keys())
    # Start each column width at the header label length.
    widths = {col: len(str(col)) for col in columns}
    for row in rows:
        for col in columns:
            widths[col] = max(widths[col], len(str(row.get(col, ""))))

    header    = " | ".join(str(col).ljust(widths[col]) for col in columns)
    separator = "-+-".join("-" * widths[col] for col in columns)
    body_rows = [
        " | ".join(str(row.get(col, "")).ljust(widths[col]) for col in columns)
        for row in rows
    ]
    return "\n".join([header, separator, *body_rows])


def handle_execute_last_sql(state: SessionState) -> None:
    """
    Option 4 — Execute Last SQL.

    Guaranteed execution order (no early returns after rows are fetched):
    ---------------------------------------------------------------
    1. Guard checks  (no SQL stored / no engine)  → return early only here
    2. Re-validate SQL
    3. Execute query
    4. Display result table
    5. Store rows in state
    6. Chart detection + optional chart generation
       — prints "Chart not suitable" OR asks user — then CONTINUES regardless
    7. Insight generation + display
       — always runs, even if chart was skipped or failed
    ---------------------------------------------------------------
    """
    logger.info("User chose option 4: Execute Last SQL")
    
    # ── Guard 1: need a SQL query ─────────────────────────────────────────
    sql = state.app_service.get_last_sql()
    if not sql:
        logger.warning("No SQL available to execute")
        print("  No SQL available. Please choose option 3 first.")
        return

    # ── Guard 2: need a database connection ───────────────────────────────
    if not state.app_service.is_database_connected():
        print("  No database connection. Please run option 1 first.")
        return

    # ── Execute query ───────────────────────────────────────────────────
    logger.info(f"Executing SQL: {sql[:100]}...")
    print(f"\n  Executing:\n  {sql}\n")

    query_context = state.app_service.get_last_query_context() or {}
    if query_context.get("warnings"):
        print("  Execution warnings:")
        for warning in query_context["warnings"]:
            print(f"  - {warning}")
        print()
    
    success, message, rows = state.app_service.execute_sql(sql, revalidate=True)
    if not success:
        print(f"  Execution failed: {message}")
        return

    if not rows:
        print("  No rows returned.")
    else:
        # Display results as a table
        print(_format_table(rows))
        print(f"\n  ({len(rows)} row{'s' if len(rows) != 1 else ''} returned)")

    # Chart generation (only if rows exist)
    if rows:
        chart_type = state.app_service.detect_chart_type(rows)
        if chart_type is None:
            print("\n  Chart not suitable for this result.")
        else:
            try:
                answer = _input(
                    f"\n  Generate chart for this result? "
                    f"(detected: {chart_type}) (y/n): "
                ).lower()
            except (KeyboardInterrupt, EOFError):
                print()
                answer = "n"

            if answer == "y":
                success, message, chart_path, chart_type_result = state.app_service.generate_chart(rows, chart_type)
                if success:
                    print(f"  Chart saved successfully: {chart_path}")
                else:
                    print(f"  Chart: {message}")

    # Insight generation — ask user if they want insights
    try:
        answer = _input(
            "\n  Do you want to generate insights for this result? (y/n): "
        ).lower()
    except (KeyboardInterrupt, EOFError):
        print()
        answer = "n"

    if answer == "y":
        print("\n  Insights:")
        success, message, insights = state.app_service.generate_insights()
        if success:
            for insight in insights:
                print(f"  • {insight}")
        else:
            print(f"  Insight generation skipped due to error: {message}")
    else:
        print("  Insights skipped.")


def handle_ai_backend_settings(state: SessionState) -> None:
    """
    AI Backend Settings submenu.
    Shows and configures the local Ollama backend.
    """
    logger.info("User chose option 5: AI Backend Settings")

    while True:
        config = state.app_service.get_backend_config()
        success, message = state.app_service.test_backend_connection()
        status = "running" if success else "not running"

        print()
        print("=" * 52)
        print("  AI Backend Settings")
        print("=" * 52)
        print("  Current backend: local")
        print(f"  Model: {config.get('model', 'llama3')}")
        print(f"  URL: {config.get('api_url', 'http://localhost:11434')}")
        print(f"  Timeout: {config.get('timeout', 60)} seconds")
        print(f"  Ollama status: {status}")
        if not success:
            print("  Ollama is not running. Using rule-based fallback when needed.")
        print("-" * 52)
        print("  1) Change local model or URL")
        print("  2) Refresh Ollama status")
        print("  3) Back")
        print("=" * 52)

        choice = _input("  Choose an option (1-3): ")

        if not choice or choice not in {"1", "2", "3"}:
            print("  Invalid option. Please choose 1 to 3.")
            continue

        if choice == "1":
            handle_use_local_llm(state)
        elif choice == "2":
            continue
        elif choice == "3":
            logger.info("User returned from AI Backend Settings")
            return


def handle_use_local_llm(state: SessionState) -> None:
    """Configure Local LLM API settings."""
    logger.info("User chose: Use Local LLM API")
    
    print("\n  Local LLM API Configuration")
    print("-" * 52)
    
    # Show current settings
    config = state.app_service.get_backend_config()
    print(f"  Current API URL: {config.get('api_url', 'Not set')}")
    print(f"  Current Model: {config.get('model', 'Not set')}")
    print(f"  Current Timeout: {config.get('timeout', 60)} seconds")
    
    # Allow user to change settings
    new_url = _input("  Enter Local LLM API URL (press Enter to keep current): ")
    new_model = _input("  Enter Model Name (press Enter to keep current): ")
    new_timeout = _input("  Enter Timeout seconds (press Enter to keep current): ")
    
    if new_url or new_model or new_timeout:
        url = new_url if new_url else config.get('api_url', 'http://localhost:11434')
        model = new_model if new_model else config.get('model', 'llama3')
        if new_timeout:
            try:
                timeout = max(int(new_timeout), 1)
            except ValueError:
                timeout = config.get('timeout', 60)
                print(f"  Invalid timeout. Keeping {timeout} seconds.")
            os.environ["LOCAL_TIMEOUT"] = str(timeout)
        state.app_service.set_local_backend(model, url)
        print(f"\n  Active backend: local ({model})")
        print(f"  API URL: {url}")
        print(f"  Timeout: {os.environ.get('LOCAL_TIMEOUT', config.get('timeout', 60))} seconds")
    else:
        print("  No changes made.")


def handle_use_nvidia(state: SessionState) -> None:
    """Configure NVIDIA API settings."""
    logger.info("User chose: Use NVIDIA API")
    
    print("\n  NVIDIA API Configuration")
    print("-" * 52)
    
    # Show current settings
    config = state.app_service.get_backend_config()
    print(f"  Current Model: {config.get('model', 'Not set')}")
    print(f"  Current Base URL: {config.get('api_url', 'Not set')}")
    
    # Get API key
    api_key = _input("  Enter NVIDIA API key (or press Enter to use NVIDIA_API_KEY from .env): ")
    if not api_key:
        api_key = os.getenv("NVIDIA_API_KEY", "")
        if not api_key:
            print("  ❌ NVIDIA_API_KEY not found in .env file.")
            print("  Please set NVIDIA_API_KEY in your .env file and try again.")
            return
    
    # Get model
    model = _input("  Enter NVIDIA model (default: meta/llama-3.1-405b-instruct): ") or "meta/llama-3.1-405b-instruct"
    
    # Get base URL (optional)
    base_url = _input("  Enter NVIDIA base URL (default: https://integrate.api.nvidia.com/v1): ") or "https://integrate.api.nvidia.com/v1"
    
    # Set backend
    state.app_service.set_nvidia_backend(model, api_key, base_url)
    
    print("\n  ✅ NVIDIA backend configured successfully!")
    print(f"  Model: {model}")
    print(f"  Base URL: {base_url}")
    print("\n  Note: API key is stored in memory for this session only.")


def handle_view_current_backend(state: SessionState) -> None:
    """View current AI backend configuration."""
    logger.info("User chose: View Current AI Backend")
    
    print("\n  Current AI Backend Configuration")
    print("-" * 52)
    
    config = state.app_service.get_backend_config()
    for key, value in config.items():
        print(f"  {key}: {value}")
    
    print("-" * 52)


def handle_test_backend_connection(state: SessionState) -> None:
    """Test current AI backend connection."""
    logger.info("User chose: Test Current AI Backend Connection")
    
    print("\n  Testing AI Backend Connection...")
    print("-" * 52)
    
    success, message = state.app_service.test_backend_connection()
    if success:
        print(f"  ✓ {message}")
    else:
        print(f"  ✗ {message}")
    
    print("-" * 52)


def handle_search_business_glossary(state: SessionState) -> None:
    """
    Phase 9 — Search Business Glossary.
    Allows users to search for business terms and see their mappings
    to database tables and columns.
    """
    logger.info("User chose option 6: Search Business Glossary")
    
    # Load the glossary using core service
    success, message, glossary = state.app_service.load_business_glossary()
    if not success:
        print(f"  {message}")
        return
    
    print(f"\n  Business glossary loaded with {len(glossary)} terms.")
    
    while True:
        search_term = _input("\n  Enter search term (or 'back' to return): ").strip()
        
        if not search_term:
            continue
        
        if search_term.lower() == "back":
            break
        
        # Search the glossary using core service
        success, message, matches = state.app_service.search_glossary(search_term)
        if not success:
            print(f"  {message}")
            continue
        
        # Display matches
        print(f"\n  Found {len(matches)} match(es) for '{search_term}':")
        print("-" * 52)
        
        for term, term_data in matches.items():
            print(f"\n  Term: {term}")
            print(f"  Description: {term_data.get('description', 'N/A')}")
            
            mapped_columns = term_data.get("mapped_columns", [])
            if mapped_columns:
                print("  Mapped columns:")
                for mapping in mapped_columns:
                    table = mapping.get("table", "")
                    column = mapping.get("column", "")
                    confidence = mapping.get("confidence", "unknown")
                    print(f"    • {table}.{column} (confidence: {confidence})")
            
            example_questions = term_data.get("example_questions", [])
            if example_questions:
                print("  Example questions:")
                for question in example_questions:
                    print(f"    • {question}")
        
        print("-" * 52)


def handle_choice(choice: int, state: SessionState) -> None:
    """
    Dispatch a validated menu choice to its handler.
    Catches all unexpected exceptions so no raw traceback ever reaches the user.
    
    Menu (CLI-only mode):
      1) Connect Database
      2) Build Knowledge Base
      3) Ask a Question
      4) Execute Last SQL
      5) AI Backend Settings
      6) Search Business Glossary
      7) Exit
    """
    try:
        if choice == 1:
            handle_connect_database(state)
        elif choice == 2:
            handle_build_knowledge_base(state)
        elif choice == 3:
            handle_ask_question(state)
        elif choice == 4:
            handle_execute_last_sql(state)
        elif choice == 5:
            handle_ai_backend_settings(state)
        elif choice == 6:
            handle_search_business_glossary(state)
        elif choice == 7:
            logger.info("User chose option 7: Exit")
            # End conversation session before exit
            try:
                state.app_service.get_conversation_memory().end_session()
                state.app_service.get_conversation_memory().save_session()
            except Exception:
                pass
            print("  Goodbye!")
            sys.exit(0)
    except SystemExit:
        raise
    except Exception as exc:
        # Last-resort catch — keeps the menu loop alive and hides tracebacks.
        logger.error(f"Unexpected error in handle_choice: {exc}")
        print(f"  Unexpected error: {exc}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    """
    Start the CLI menu loop.
    Loads .env so environment variables are available from the first prompt.
    """
    load_dotenv()
    logger.info("AI SQL Query Generator started")
    state = SessionState()

    while True:
        display_menu(state)
        choice = read_menu_choice()
        if choice is None:
            continue
        handle_choice(choice, state)


if __name__ == "__main__":
    main()
