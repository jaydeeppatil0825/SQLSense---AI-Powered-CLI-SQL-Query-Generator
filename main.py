"""
main.py
=======
CLI entry point for the AI SQL Query Generator.

Menu flow
---------
  1) Connect / Reuse Database  — connect once, save for reuse
  2) Ask Question              — auto-executes safe validated SQL
  3) Rebuild Knowledge Base    — force KB/glossary/vector rebuild
  4) Semantic AI Settings      — view KB enrichment backend status
  5) Search Business Glossary  — search business terms
  6) Show Current Connection   — view active connection details
  7) Exit

All exceptions are caught at the boundary of each handler so the user
never sees a raw Python traceback — only a clean one-line error message.

Phase 5: CLI is now thin - business logic moved to core services.
CLI only handles menu display, input collection, and output formatting.

CLI UX Phase: Improved menu display, connection reuse, and auto-execution
of safe validated SQL queries.
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
from kb_pipeline.connection import SUPPORTED_DB_TYPES
from utils.logger import get_logger
from utils.config_manager import (
    save_connection_config,
    load_connection_config,
    has_saved_connection,
    format_connection_summary,
)

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
    """Read AI_BACKEND from env; fall back to local for unknown values."""
    backend = (os.getenv("AI_BACKEND") or os.getenv("LLM_BACKEND") or "local").strip().lower()
    return backend if backend in {"local", "nvidia"} else "local"


def _backend_label(state: SessionState) -> str:
    """One-line label for the active AI backend shown in the menu header."""
    config = state.app_service.get_backend_config()
    backend = config.get("active_backend", _current_backend_from_env())
    return f"{backend} ({config.get('model', 'unknown')})"


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


def _as_dict(value: object) -> dict:
    """Return a safe dict for CLI display code."""
    return value if isinstance(value, dict) else {}


# ── Menu display ──────────────────────────────────────────────────────────────

def display_menu(state: SessionState) -> None:
    """Print the full CLI menu with current session context in the header."""
    print()
    print("+" + "=" * 62 + "+")
    print("|  " + "SQLSense - AI SQL Query Generator".center(58) + "  |")
    print("+" + "=" * 62 + "+")
    
    # Database status
    db_status = _db_label(state)
    db_icon = "+" if state.app_service.is_database_connected() else "x"
    print(f"|  [{db_icon}] Database : {db_status:<45}|")
    
    # KB/Vector status
    if state.app_service.is_database_connected():
        kb_ready = state.app_service.is_database_ready()
        kb_icon = "+" if kb_ready else "x"
        kb_status = "ready" if kb_ready else "not ready"
        print(f"|  [{kb_icon}] Knowledge Base : {kb_status:<39}|")
        
        vector_status = state.app_service.get_vector_status()
        vector_index = vector_status.get("index_status", "unknown") if vector_status else "unknown"
        vector_icon = "+" if vector_index == "ready" else "o"
        print(f"|  [{vector_icon}] Vector Index : {vector_index:<41}|")
    
    # Backend status
    backend = _backend_label(state)
    print(f"|  [o] AI Backend : {backend:<43}|")
    
    print("+" + "-" * 62 + "+")
    print("|  1. Connect / Reuse Database                               |")
    print("|  2. Ask Question (auto-executes safe SQL)                  |")
    print("|  3. Rebuild Knowledge Base                                 |")
    print("|  4. Semantic AI Settings                                   |")
    print("|  5. Search Business Glossary                               |")
    print("|  6. Show Current Connection                                |")
    print("|  7. Exit                                                   |")
    print("+" + "=" * 62 + "+")


def read_menu_choice() -> int | None:
    """
    Read a menu choice from the user and return it as an integer (1–7).

    Uses plain input() via _input() — no special key libraries, no raw
    terminal mode.  The user types a digit and presses Enter normally.

    Returns None on empty or invalid input so the caller can loop again.
    """
    raw = _input("\n  Choose an option (1-7): ")

    if not raw:
        print("  Please enter a menu option.")
        return None

    if raw not in {"1", "2", "3", "4", "5", "6", "7"}:
        print(f"  Invalid option '{raw}'. Please choose 1 to 7.")
        return None

    return int(raw)


# ── Option handlers ───────────────────────────────────────────────────────────

def _print_database_prepare_report(state: SessionState, report: dict[str, object]) -> None:
    """Print database preparation progress/results after connect or rebuild."""
    report = _as_dict(report)
    print("\n  Preparation status:")
    print(f"  - connecting database: {'done' if report.get('connected') else 'failed'}")
    print(f"  - building KB: {'done' if report.get('kb_built') else 'failed'}")
    print(f"  - AI enrichment: {report.get('ai_enrichment_status')} ({report.get('ai_enrichment_message')})")
    print(f"  - glossary generated: {'yes' if report.get('glossary_generated') else 'no'}")
    vector_status = _as_dict(state.app_service.get_vector_status() or {})
    print(f"  - vector built/skipped: {vector_status.get('index_status')}")
    persistence = _as_dict(vector_status.get("persistence") or {})
    if report.get("vector_warning"):
        print(f"  - vector note: {report.get('vector_warning')}")
    elif persistence.get("stale_reason"):
        print(f"  - vector note: {persistence.get('stale_reason')}")
    if persistence.get("source"):
        print(f"  - vector source: {persistence.get('source')}")
    print(f"  - database ready: {'yes' if report.get('database_ready') else 'no'}")


def handle_connect_database(state: SessionState) -> None:
    """
    Option 1 — Connect Database / Reuse Last Connection.
    
    Supports:
    - Reusing last saved connection
    - Entering new connection details
    - Auto-building KB after connection
    
    Password is collected via getpass and never written to any file.
    """
    logger.info("User chose option 1: Connect / Reuse Database")
    
    # Check if we have a saved connection
    saved_config = load_connection_config()
    
    if saved_config and has_saved_connection():
        print(f"\n  Last connection: {format_connection_summary(saved_config)}")
        reuse = _input("  Reuse last connection? [Y/n]: ").lower()
        
        if reuse in {"", "y", "yes"}:
            # Reuse saved connection
            db_type = saved_config.get("db_type", "mysql")
            
            if db_type == "sqlite":
                sqlite_path = saved_config.get("sqlite_path", "")
                print(f"\n  Connecting to SQLite: {sqlite_path}")
                print("  Building knowledge base...")
                success, message, report = state.app_service.connect_database_and_prepare(
                    db_type="sqlite",
                    sqlite_path=sqlite_path,
                    use_ai_enrichment=True,
                )
                _print_database_prepare_report(state, report)
                if success:
                    print("  [+] Database ready.")
                else:
                    print(f"  [x] Preparation failed: {message}")
                return
            
            # MySQL/PostgreSQL - need password
            host = saved_config.get("host", "localhost")
            port = saved_config.get("port", 3306)
            username = saved_config.get("username", "")
            database = saved_config.get("database", "")
            
            # Try password from environment first
            password = os.getenv("DB_PASSWORD", "")
            if not password:
                try:
                    sys.stdout.flush()
                    password = getpass.getpass("  Password: ")
                    sys.stdout.flush()
                except (KeyboardInterrupt, EOFError):
                    print("\n  Password input cancelled.")
                    return
            
            print(f"\n  Connecting to {db_type}://{username}@{host}:{port}/{database}...")
            print("  Building knowledge base...")
            success, message, report = state.app_service.connect_database_and_prepare(
                db_type=db_type,
                host=host,
                port=port,
                username=username,
                password=password,
                database=database,
                use_ai_enrichment=True,
            )
            _print_database_prepare_report(state, report)
            if success:
                logger.info(f"Successfully reconnected to {db_type}://{username}@{host}:{port}/{database}")
                print("  [+] Database ready.")
            else:
                logger.error(f"Database reconnect failed: {message}")
                print(f"  [x] Preparation failed: {message}")
            return
    
    # No saved connection or user chose to enter new details
    print(f"\n  Supported database types: mysql")
    print("  PostgreSQL and SQLite are planned for future phases.\n")

    db_type = _prompt("Database type", "mysql").lower()
    if db_type not in SUPPORTED_DB_TYPES:
        print(f"  [x] Unsupported type '{db_type}'. Only mysql is currently supported.")
        return

    # ── SQLite path ──────────────────────────────────────────────────────────
    if db_type == "sqlite":
        sqlite_path = _prompt("SQLite file path")
        if not sqlite_path:
            print("  [x] SQLite file path cannot be empty.")
            return
        print("\n  Connecting to database...")
        print("  Building knowledge base...")
        success, message, report = state.app_service.connect_database_and_prepare(
            db_type="sqlite",
            sqlite_path=sqlite_path,
            use_ai_enrichment=True,
        )
        _print_database_prepare_report(state, report)
        if not success:
            print(f"  [x] Preparation failed: {message}")
            return
        
        # Save connection config
        save_connection_config(db_type=db_type, sqlite_path=sqlite_path)
        print("  [+] Database ready. Connection saved for next session.")
        return

    # ── MySQL / PostgreSQL ───────────────────────────────────────────────────
    host     = _prompt("Host", "localhost")
    port     = _prompt_int("Port", _DEFAULT_PORTS.get(db_type, 3306))
    username = _prompt("Username")
    if not username:
        print("  [x] Username cannot be empty.")
        return
    database = _prompt("Database name")
    if not database:
        print("  [x] Database name cannot be empty.")
        return

    # Get password securely
    try:
        sys.stdout.flush()
        password = getpass.getpass("  Password: ")
        sys.stdout.flush()
    except (KeyboardInterrupt, EOFError):
        print("\n  Password input cancelled.")
        return

    print(f"\n  Connecting to {db_type}://{username}@{host}:{port}/{database}...")
    print("  Building knowledge base...")
    success, message, report = state.app_service.connect_database_and_prepare(
        db_type=db_type,
        host=host,
        port=port,
        username=username,
        password=password,
        database=database,
        use_ai_enrichment=True,
    )
    _print_database_prepare_report(state, report)
    if not success:
        logger.error(f"Database connect/prepare failed: {message}")
        print(f"  [x] Preparation failed: {message}")
        return

    # Save connection config (password excluded)
    save_connection_config(
        db_type=db_type,
        host=host,
        port=port,
        username=username,
        database=database,
    )
    logger.info(f"Successfully connected and prepared database: {db_type}://{username}@{host}:{port}/{database}")
    print("  [+] Database ready. Connection saved for next session.")
    print("  Note: Password is not saved. Set DB_PASSWORD in .env or enter when prompted.")


def handle_rebuild_or_refresh_knowledge_base(state: SessionState) -> None:
    """
    Option 3 — Rebuild Knowledge Base.
    Force rebuild of KB/glossary/vector assets for the active database.
    """
    logger.info("User chose option 3: Rebuild Knowledge Base")
    if not state.app_service.is_database_connected():
        print("  [x] No database connection. Please run option 1 first.")
        return

    print("\n  Building knowledge base...")
    success, message, knowledge_base = state.app_service.rebuild_or_refresh_knowledge_base(
        use_ai_enrichment=True,
        ai_backend=state.app_service.get_active_backend(),
    )
    if not success:
        logger.error(f"Knowledge base build failed: {message}")
        print(f"  [x] Knowledge base build failed: {message}")
        return

    enrichment_status, enrichment_message = state.app_service.get_last_ai_enrichment_result()
    if enrichment_status == "completed":
        print("  [+] AI enrichment completed successfully")
    elif enrichment_status == "partial":
        print(f"  [o] {enrichment_message}")
    else:
        print(f"  [o] AI enrichment skipped/fallback used ({enrichment_message})")

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

    print(f"\n  [+] Knowledge base saved → semantic/knowledge_base.json")
    print(f"  [+] Business glossary saved → semantic/business_glossary.json")
    
    vector_status = state.app_service.get_vector_status()
    if vector_status:
        embedding_status = vector_status.get("embedding", {})
        retriever_status = vector_status.get("retriever", {})
        persistence_status = vector_status.get("persistence", {})
        print("\n  Vector / Embedding Status:")
        print(f"  - index status: {vector_status.get('index_status')}")
        print(f"  - index source: {persistence_status.get('source')}")
        print(f"  - index fresh: {persistence_status.get('is_fresh')}")
        print(f"  - embedding backend: {embedding_status.get('backend')}")
        print(f"  - embedding model: {embedding_status.get('model')}")
        print(f"  - fallback used: {embedding_status.get('fallback_used')}")
        print(f"  - indexed documents: {retriever_status.get('document_count', 0)}")
        if persistence_status.get("stale_reason"):
            print(f"  - stale reason: {persistence_status.get('stale_reason')}")
        if persistence_status.get("persistence_error"):
            print(f"  - persistence note: {persistence_status.get('persistence_error')}")
        if embedding_status.get("init_error"):
            print(f"  - backend note: {embedding_status.get('init_error')}")
    
    print("\n  [+] Database ready.")


def handle_ask_question(state: SessionState) -> None:
    """
    Option 2 — Ask a Question (Auto-Execute Safe SQL).

    Flow
    ----
    1. Load the knowledge base.
    2. Receive the user's question.
    3. Check action detector (chart, insights, new_chat, etc.).
    4. If action exists, handle action and return to menu.
    5. Process question using core service.
    6. Display the SQL and context.
    7. If SQL is safe and validated, auto-execute and show results.
    8. Save conversation session.
    
    Auto-execution rules:
    - Only execute if validation passes
    - Only execute if route is deterministic (rule-based)
    - Never execute if blocked_unsafe or cannot_plan_safely
    - Never execute invalid, ambiguous, or unsafe SQL
    """
    logger.info("User chose option 2: Ask a Question")
    
    # ── Load knowledge base ───────────────────────────────────────────────
    if not state.app_service.is_database_connected():
        print("  [x] No database connection. Please run option 1 first.")
        return

    if not state.app_service.is_database_ready():
        print("  [x] Database is not ready. Connect a database first (option 1).")
        return

    success, message, knowledge_base = state.app_service.load_knowledge_base()
    if not success:
        print(f"  [x] {message}")
        return

    # ── Get the question ──────────────────────────────────────────────────
    question = _input("\n  Enter your question: ")
    if not question:
        logger.warning("Empty question submitted")
        print("  [x] Question cannot be empty.")
        return
    if len(question) > 500:
        logger.warning(f"Question too long: {len(question)} characters")
        print("  [x] Question is too long — please keep it to 500 characters or fewer.")
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
    result = state.app_service.process_question(question, ai_backend)
    if result is None:
        logger.error("Ask Question returned no result payload")
        print("  [x] Internal error: question processing returned no result.")
        return
    if not isinstance(result, dict):
        logger.error(f"Ask Question returned invalid result payload: {type(result).__name__}")
        print("  [x] Internal error: question processing returned an invalid result.")
        return

    success = bool(result.get("success"))
    message = str(result.get("message") or "")
    error = result.get("error")
    sql = result.get("sql") or result.get("generated_sql")
    route_used = str(result.get("route_used") or result.get("route") or "").strip()
    validation_result = result.get("validation_result") or {}
    is_valid = validation_result.get("is_valid", False)
    
    if not success:
        print(f"\n  [x] {error or message}")
        return
    
    if not sql:
        logger.error("Ask Question succeeded without SQL in result payload")
        print("  [x] Internal error: SQL generation succeeded without a SQL result.")
        return

    # ── Display the SQL and context ───────────────────────────────────────
    query_context = result.get("query_context") or state.app_service.get_last_query_context() or {}
    
    print("\n" + "─" * 60)
    print("  Generated SQL:")
    print("─" * 60)
    print(f"  {sql}")
    print("─" * 60)
    
    # Show route information (compact)
    route_labels = {
        "rule-based": "[+] Rule-based (deterministic)",
        "rule_based": "[+] Rule-based (deterministic)",
        "simple_rule_based": "[+] Rule-based (deterministic)",
        "simple-rule-based": "[+] Rule-based (deterministic)",
        "deterministic_sql_required": "[+] Deterministic SQL",
        "cannot_plan_safely": "[x] Cannot plan safely",
        "blocked_unsafe": "[x] Unsafe query blocked",
        "ai": "[o] AI-generated",
        "ai-retry": "[o] AI retry",
    }
    route_display = route_labels.get(route_used, f"[o] {route_used}")
    print(f"  Route: {route_display}")
    
    # Show validation status
    if is_valid:
        print(f"  Validation: [+] Passed")
    else:
        reason = validation_result.get("reason", "unknown")
        print(f"  Validation: [x] Failed - {reason}")
    
    # Show warnings if any
    warnings = query_context.get("warnings", [])
    if warnings:
        print(f"  Warnings: {len(warnings)} issue(s)")
        for warning in warnings[:3]:
            print(f"    • {warning}")
    
    print("─" * 60)
    
    # ── Auto-execute decision ──────────────────────────────────────────────
    can_auto_execute = (
        is_valid
        and route_used in {"rule-based", "rule_based", "simple_rule_based", "simple-rule-based", "deterministic_sql_required"}
        and route_used not in {"cannot_plan_safely", "blocked_unsafe"}
    )
    
    if not can_auto_execute:
        if route_used in {"cannot_plan_safely", "blocked_unsafe"}:
            print(f"\n  [!] Auto-execution skipped: {route_used.replace('_', ' ')}")
        elif not is_valid:
            print(f"\n  [!] Auto-execution skipped: SQL validation failed")
        else:
            print(f"\n  [!] Auto-execution skipped: Route '{route_used}' is not deterministic")
        
        # Save conversation session
        conversation_memory = state.app_service.get_conversation_memory()
        conversation_memory.save_session()
        return
    
    # ── Auto-execute safe validated SQL ────────────────────────────────────
    print(f"\n  > Auto-executing safe validated SQL...")
    logger.info(f"Auto-executing SQL: {sql[:100]}...")
    
    exec_success, exec_message, rows = state.app_service.execute_sql(sql, revalidate=True)
    if not exec_success:
        print(f"  [x] Execution failed: {exec_message}")
        # Save conversation session
        conversation_memory = state.app_service.get_conversation_memory()
        conversation_memory.save_session()
        return

    if not rows:
        print("  [+] Query executed successfully. No rows returned.")
    else:
        # Display results as a table
        print("\n" + "─" * 60)
        print("  Results:")
        print("─" * 60)
        print(_format_table(rows))
        print("─" * 60)
        print(f"  [+] {len(rows)} row{'s' if len(rows) != 1 else ''} returned")
        print("─" * 60)
    
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


def handle_show_current_connection(state: SessionState) -> None:
    """
    Option 6 — Show Current Connection.
    Displays detailed information about the active database connection.
    """
    logger.info("User chose option 6: Show Current Connection")
    
    if not state.app_service.is_database_connected():
        print("\n  [x] No active database connection.")
        saved_config = load_connection_config()
        if saved_config:
            print(f"\n  Last saved connection: {format_connection_summary(saved_config)}")
            print("  Use option 1 to reconnect.")
        return
    
    db_config = state.app_service.database_service.get_db_config()
    if not db_config:
        print("\n  [x] No database configuration available.")
        return
    
    print("\n" + "═" * 60)
    print("  Current Database Connection")
    print("═" * 60)
    
    db_type = db_config.get("db_type", "unknown")
    
    if db_type == "sqlite":
        sqlite_path = db_config.get("sqlite_path", "")
        print(f"  Type: SQLite")
        print(f"  Path: {sqlite_path}")
    else:
        host = db_config.get("host", "")
        port = db_config.get("port", "")
        username = db_config.get("username", "")
        database = db_config.get("database", "")
        print(f"  Type: {db_type}")
        print(f"  Host: {host}")
        print(f"  Port: {port}")
        print(f"  Username: {username}")
        print(f"  Database: {database}")
    
    print("─" * 60)
    
    # Show KB and vector status
    kb_ready = state.app_service.is_database_ready()
    print(f"  Knowledge Base: {'[+] Ready' if kb_ready else '[x] Not ready'}")
    
    vector_status = state.app_service.get_vector_status()
    if vector_status:
        index_status = vector_status.get("index_status", "unknown")
        print(f"  Vector Index: {index_status}")
        
        embedding = vector_status.get("embedding", {})
        if embedding:
            backend = embedding.get("backend", "unknown")
            model = embedding.get("model", "unknown")
            print(f"  Embedding: {backend} ({model})")
    
    print("═" * 60)


def handle_ai_backend_settings(state: SessionState) -> None:
    """
    Semantic AI Settings submenu.
    Shows and configures the KB enrichment backend only.
    """
    logger.info("User chose option 4: Semantic AI Settings")
    last_test_success: bool | None = None
    last_test_message: str | None = None

    while True:
        config = state.app_service.get_backend_config()
        backend_name = config.get("active_backend", "local")
        status = "not tested"
        if last_test_success is not None:
            status = "connected" if last_test_success else "not connected"

        print()
        print("=" * 52)
        print("  Semantic AI Settings")
        print("=" * 52)
        print(f"  Current backend: {backend_name}")
        print(f"  Model: {config.get('model', 'unknown')}")
        print(f"  URL: {config.get('api_url', 'not set')}")
        if backend_name == "nvidia":
            print(f"  Temperature: {config.get('temperature', 0)}")
            print(f"  Max tokens: {config.get('max_tokens', 2048)}")
        print(f"  Timeout: {config.get('timeout', 60)} seconds")
        print(f"  Backend status: {status}")
        if last_test_message:
            print(f"  Last test: {last_test_message}")
            if last_test_success is False and backend_name == "local":
                print("  Ollama is not running. Using rule-based fallback when needed.")
        elif backend_name == "local":
            print("  No backend test run yet. Local Ollama remains the default backend.")
        else:
            print("  No backend test run yet. NVIDIA is optional and tested only on demand.")
        print("-" * 52)
        print("  1) Use local backend")
        print("  2) Use NVIDIA backend")
        print("  3) Test active backend")
        print("  4) Refresh status")
        print("  5) Back")
        print("=" * 52)

        choice = _input("  Choose an option (1-5): ")

        if not choice or choice not in {"1", "2", "3", "4", "5"}:
            print("  Invalid option. Please choose 1 to 5.")
            continue

        if choice == "1":
            handle_use_local_llm(state)
        elif choice == "2":
            handle_use_nvidia(state)
        elif choice == "3":
            last_test_success, last_test_message = handle_test_backend_connection(state)
        elif choice == "4":
            last_test_success, last_test_message = state.app_service.test_backend_connection()
            continue
        elif choice == "5":
            logger.info("User returned from Semantic AI Settings")
            return


def handle_use_local_llm(state: SessionState) -> None:
    """Configure Local LLM API settings."""
    logger.info("User chose: Use Local LLM API")
    
    print("\n  Local LLM API Configuration")
    print("-" * 52)
    
    config = state.app_service.get_backend_config()
    current_url = config.get("api_url", "http://localhost:11434")
    current_model = config.get("model", "llama3")
    current_timeout = config.get("timeout", 60)
    print(f"  Current API URL: {current_url}")
    print(f"  Current Model: {current_model}")
    print(f"  Current Timeout: {current_timeout} seconds")
    
    # Allow user to change settings
    new_url = _input("  Enter Local LLM API URL (press Enter to keep current): ")
    new_model = _input("  Enter Model Name (press Enter to keep current): ")
    new_timeout = _input("  Enter Timeout seconds (press Enter to keep current): ")
    
    if new_url or new_model or new_timeout:
        url = new_url if new_url else current_url
        model = new_model if new_model else current_model
        if new_timeout:
            try:
                timeout = max(int(new_timeout), 1)
            except ValueError:
                timeout = current_timeout
                print(f"  Invalid timeout. Keeping {timeout} seconds.")
            os.environ["LOCAL_TIMEOUT"] = str(timeout)
        state.app_service.set_local_backend(model, url)
        print(f"\n  Active backend: local ({model})")
        print(f"  API URL: {url}")
        print(f"  Timeout: {os.environ.get('LOCAL_TIMEOUT', current_timeout)} seconds")
    else:
        print("  No changes made.")


def handle_use_nvidia(state: SessionState) -> None:
    """Configure NVIDIA API settings."""
    logger.info("User chose: Use NVIDIA API")
    
    print("\n  NVIDIA API Configuration")
    print("-" * 52)
    
    current_config = state.app_service.get_backend_config()
    current_model = os.getenv("NVIDIA_MODEL", current_config.get("model", "nvidia/nemotron-3-ultra-550b-a55b"))
    current_base_url = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
    current_temperature = os.getenv("NVIDIA_TEMPERATURE", "1")
    current_max_tokens = os.getenv("NVIDIA_MAX_TOKENS", "16384")
    print(f"  Current Model: {current_model}")
    print(f"  Current Base URL: {current_base_url}")
    print(f"  Current Temperature: {current_temperature}")
    print(f"  Current Max Tokens: {current_max_tokens}")
    
    # Get API key
    api_key = _input("  Enter NVIDIA API key (or press Enter to use NVIDIA_API_KEY from .env): ")
    if not api_key:
        api_key = os.getenv("NVIDIA_API_KEY", "")
        if not api_key:
            print("  ❌ NVIDIA_API_KEY not found in .env file.")
            print("  Please set NVIDIA_API_KEY in your .env file and try again.")
            return
    
    # Get model
    model = _input(f"  Enter NVIDIA model (default: {current_model}): ") or current_model
    
    # Get base URL (optional)
    base_url = _input(f"  Enter NVIDIA base URL (default: {current_base_url}): ") or current_base_url
    
    # Set backend
    state.app_service.set_nvidia_backend(model, api_key, base_url)
    
    print("\n  ✅ NVIDIA backend configured successfully!")
    print(f"  Model: {model}")
    print(f"  Base URL: {base_url}")
    print("\n  Note: API key is read from env unless you override it for this session.")


def handle_view_current_backend(state: SessionState) -> None:
    """View current AI backend configuration."""
    logger.info("User chose: View Current AI Backend")
    
    print("\n  Current AI Backend Configuration")
    print("-" * 52)
    
    config = state.app_service.get_backend_config()
    for key, value in config.items():
        print(f"  {key}: {value}")
    
    print("-" * 52)


def handle_test_backend_connection(state: SessionState) -> tuple[bool, str]:
    """Test current AI backend connection."""
    logger.info("User chose: Test Current AI Backend Connection")
    
    print("\n  Testing AI Backend Connection...")
    print("-" * 52)
    
    success, message = state.app_service.test_backend_connection()
    if success:
        print(f"  [+] {message}")
    else:
        print(f"  [x] {message}")
    
    print("-" * 52)
    return success, message


def handle_search_business_glossary(state: SessionState) -> None:
    """
    Option 5 — Search Business Glossary.
    Allows users to search for business terms and see their mappings
    to database tables and columns.
    """
    logger.info("User chose option 5: Search Business Glossary")
    
    # Load the glossary using core service
    success, message, glossary = state.app_service.load_business_glossary()
    if not success:
        print(f"  [x] {message}")
        return
    
    print(f"\n  [+] Business glossary loaded with {len(glossary)} terms.")
    
    while True:
        search_term = _input("\n  Enter search term (or 'back' to return): ").strip()
        
        if not search_term:
            continue
        
        if search_term.lower() == "back":
            break
        
        # Search the glossary using core service
        success, message, matches = state.app_service.search_glossary(search_term)
        if not success:
            print(f"  [x] {message}")
            continue
        
        # Display matches
        print(f"\n  Found {len(matches)} match(es) for '{search_term}':")
        print("─" * 60)
        
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
                for question in example_questions[:3]:
                    print(f"    • {question}")
        
        print("─" * 60)


def handle_choice(choice: int, state: SessionState) -> None:
    """
    Dispatch a validated menu choice to its handler.
    Catches all unexpected exceptions so no raw traceback ever reaches the user.
    
    Menu:
      1) Connect / Reuse Database
      2) Ask Question (auto-executes safe SQL)
      3) Rebuild Knowledge Base
      4) Semantic AI Settings
      5) Search Business Glossary
      6) Show Current Connection
      7) Exit
    """
    try:
        if choice == 1:
            handle_connect_database(state)
        elif choice == 2:
            handle_ask_question(state)
        elif choice == 3:
            handle_rebuild_or_refresh_knowledge_base(state)
        elif choice == 4:
            handle_ai_backend_settings(state)
        elif choice == 5:
            handle_search_business_glossary(state)
        elif choice == 6:
            handle_show_current_connection(state)
        elif choice == 7:
            logger.info("User chose option 7: Exit")
            # End conversation session before exit
            try:
                state.app_service.get_conversation_memory().end_session()
                state.app_service.get_conversation_memory().save_session()
            except Exception as exc:
                # Log but don't block exit if session cleanup fails
                logger.debug(f"Failed to save conversation session on exit: {exc}")
            print("\n  Goodbye! ")
            sys.exit(0)
    except SystemExit:
        raise
    except Exception as exc:
        # Last-resort catch — keeps the menu loop alive and hides tracebacks.
        logger.error(f"Unexpected error in handle_choice: {exc}")
        print(f"  [x] Unexpected error: {exc}")


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

