"""
ai/sql_generator.py
====================
Dispatches SQL generation requests to the configured AI backend
(local Ollama) and returns a clean, validated SQL string.

Flow
----
  1. build_sql_prompt()   — assemble the prompt with full schema context
  2. _call_ollama()  — send to local AI, get raw response
  3. _clean_sql_response()             — strip fences, explanations, whitespace
  4. Return the cleaned SQL string

The caller validates the returned SQL and may apply an explicit user-requested
LIMIT only after the SQL is structurally valid.

Debug mode
----------
Set DEBUG_PROMPT=true in .env to have prompt_builder.py print the full
system prompt before it is sent to the model.
"""

from __future__ import annotations

import os
import re

from dotenv import load_dotenv

from ai.prompt_builder import build_sql_prompt
from utils.logger import get_logger
from utils.sql_validator import clean_sql_response

try:
    import requests
except ImportError:                     # pragma: no cover
    requests = None


load_dotenv()
logger = get_logger()


# ── SQL extraction and cleanup ────────────────────────────────────────────────

def _repair_order_by(sql: str) -> str:
    """
    Fix malformed ORDER BY clauses that some models produce.

    Patterns repaired
    -----------------
    1. "ORDER BY LIMIT n"  → "LIMIT n"   (missing column before LIMIT)
    2. "ORDER BY ;"        → removed     (missing column before semicolon)
    3. "ORDER BY" at end   → removed     (dangling keyword, nothing follows)
    """
    # Pattern 1: ORDER BY immediately followed by LIMIT
    sql = re.sub(r"\bORDER\s+BY\s+(?=LIMIT\b)", "", sql, flags=re.IGNORECASE)
    # Pattern 2: ORDER BY immediately followed by semicolon or end-of-string
    sql = re.sub(r"\bORDER\s+BY\s*(?=;|$)", "", sql, flags=re.IGNORECASE)
    # Collapse extra whitespace left by the removals
    sql = re.sub(r"[ \t]{2,}", " ", sql)
    sql = re.sub(r"\n{3,}", "\n\n", sql)
    return sql.strip()


# Common preamble phrases models write before the actual SQL.
# These are matched case-insensitively at the start of lines and stripped.
_PREAMBLE_PATTERNS = re.compile(
    r"^("
    r"here\s+is(\s+the)?\s+sql[\s:]*"          # "Here is the SQL:"
    r"|here\s+is(\s+a)?\s+query[\s:]*"          # "Here is a query:"
    r"|sql\s+statement[\s\w]*:+"                # "SQL statement to show X:"
    r"|sql\s+query[\s\w]*:+"                    # "SQL query:"
    r"|the\s+sql[\s\w]*:+"                      # "The SQL:"
    r"|query[\s:]*"                             # "Query:"
    r"|result[\s:]*"                            # "Result:"
    r"|output[\s:]*"                            # "Output:"
    r"|answer[\s:]*"                            # "Answer:"
    r")\s*",
    re.IGNORECASE,
)


def extract_sql_only(response_text: str) -> str:
    """
    Extract the first clean SELECT statement from a raw AI response.

    The model sometimes wraps its answer in:
    - Markdown fences:  ```sql ... ```  or  ``` ... ```
    - Preamble labels:  "Here is the SQL:", "SQL statement to show requested rows:"
    - Trailing notes:   "This query joins…" after the semicolon

    This function handles all of those cases and returns only the SQL.

    Steps (applied in order)
    -------------------------
    1. Strip leading/trailing whitespace.
    2. Remove ALL markdown code fences (opening and closing), even if
       they appear in the middle of the response.
    3. Split into lines and drop any line that is pure preamble text
       (matches _PREAMBLE_PATTERNS) BEFORE the SELECT keyword appears.
    4. Find the first SELECT keyword — discard everything before it.
    5. Find where the first SQL statement ends:
       - At a semicolon (kept in output)
       - OR at the first blank line after SQL content starts
       - OR at the first line that looks like a plain-English sentence
         after a SQL line
    6. Apply _repair_order_by() to fix malformed ORDER BY patterns.
    7. Strip and return.

    Args:
        response_text: Raw string returned by the AI backend.

    Returns:
        A clean SQL string with no preamble, no fences, no trailing text.
        Returns an empty string if no SELECT is found.
    """
    text = str(response_text or "").strip()

    # ── Step 1: Remove ALL markdown fences ───────────────────────────────────
    # Remove ```sql or ``` wherever they appear (not just at edges).
    text = re.sub(r"```(?:sql)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"```", "", text)
    text = text.strip()

    # ── Step 2: Find first SELECT ─────────────────────────────────────────────
    select_match = re.search(r"\bSELECT\b", text, re.IGNORECASE)
    if not select_match:
        # No SELECT found at all — return empty so validator rejects it cleanly.
        return ""

    # Everything before SELECT is preamble — drop it.
    text = text[select_match.start():]

    # ── Step 3: Cut off after the first statement ends ────────────────────────
    # Strategy: walk the text character by character looking for a semicolon.
    # If there is no semicolon, walk line by line and stop at the first line
    # that looks like a plain-English sentence after SQL content.

    sql_lines: list[str] = []
    found_end = False

    # A SQL-looking line: starts with a SQL keyword, identifier, punctuation,
    # or whitespace continuation.  Used to detect where trailing prose begins.
    sql_line_re = re.compile(
        r"^\s*("
        r"SELECT|FROM|WHERE|JOIN|LEFT|RIGHT|INNER|OUTER|CROSS|FULL|"
        r"ON|GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|OFFSET|UNION|WITH|"
        r"AS|AND|OR|NOT|IN|EXISTS|BETWEEN|LIKE|IS\s+NULL|IS\s+NOT|"
        r"CASE|WHEN|THEN|ELSE|END|"
        r"COUNT|SUM|AVG|MAX|MIN|DISTINCT|COALESCE|IFNULL|IF\s*\(|"
        r"DATE_FORMAT|DATE|YEAR|MONTH|DAY|NOW|CURDATE|"
        r"--|\(|\)|\w+\s*[=<>!]|\w+\.\w+|`\w"
        r")",
        re.IGNORECASE,
    )

    for line in text.splitlines():
        stripped = line.strip()

        # Empty line after we already have SQL lines = end of statement.
        if not stripped:
            if sql_lines:
                found_end = True
                break
            continue

        # Line ends with (or contains) a semicolon = end of statement.
        if ";" in stripped:
            # Keep only up to and including the first semicolon on this line.
            semicolon_pos = stripped.index(";")
            sql_lines.append(stripped[: semicolon_pos + 1])
            found_end = True
            break

        # If we already have SQL lines and this line looks like prose, stop.
        if sql_lines and not sql_line_re.match(line):
            found_end = True
            break

        sql_lines.append(line)

    sql = "\n".join(sql_lines).strip()

    # ── Step 4: Repair ORDER BY problems ─────────────────────────────────────
    sql = _repair_order_by(sql)

    return sql.strip()


def _clean_sql_response(raw: str) -> str:
    """     
    Public-facing alias kept for backward compatibility.
    Delegates entirely to extract_sql_only().
    """
    return clean_sql_response(raw)


def _local_api_url() -> str:
    return (os.getenv("LOCAL_API_URL") or "http://localhost:11434").strip().rstrip("/")


def _local_timeout() -> int:
    raw = (os.getenv("LOCAL_TIMEOUT") or "120").strip()
    try:
        timeout = int(raw)
    except ValueError:
        return 60
    return max(timeout, 1)


def check_ollama_status(api_url: str | None = None, timeout: int = 5) -> tuple[bool, str]:
    """Return whether the local Ollama server is reachable."""
    if requests is None:
        return False, "The 'requests' package is required to check Ollama."

    base_url = (api_url or _local_api_url()).rstrip("/")
    try:
        response = requests.get(f"{base_url}/api/tags", timeout=timeout)
        if response.status_code != 200:
            return False, "Ollama is not running."
        return True, "Ollama is running."
    except Exception:
        return False, "Ollama is not running."


# ── Backend dispatchers ───────────────────────────────────────────────────────

def _call_ollama(messages: list[dict], response_format: dict | str | None = None) -> str:
    """
    POST the message list to the local Ollama /api/chat endpoint.

    Reads the model name from LOCAL_MODEL env var (default: llama3).
    Raises ConnectionError if the Ollama server is not running.
    """
    if requests is None:
        raise RuntimeError(
            "The 'requests' package is required for the local backend. "
            "Run: pip install requests"
        )

    model = (os.getenv("LOCAL_MODEL") or "llama3").strip() or "llama3"
    base_url = _local_api_url()
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        # temperature=0 makes output deterministic (same question → same SQL)
        "options": {"temperature": 0, "num_predict": 300},
    }
    if response_format:
        payload["format"] = response_format

    try:
        response = requests.post(
            f"{base_url}/api/chat",
            json=payload,
            timeout=_local_timeout(),
        )
        response.raise_for_status()
        data = response.json()

        # Ollama wraps the reply in data["message"]["content"]
        if "message" in data and isinstance(data["message"], dict):
            return data["message"].get("content", "")

        # Fallback: some older Ollama versions use OpenAI-style response
        if data.get("choices"):
            return data["choices"][0].get("message", {}).get("content", "")

        raise RuntimeError("Ollama response did not contain generated SQL.")

    except ConnectionError:
        raise
    except requests.exceptions.Timeout as exc:
        raise TimeoutError("Local AI timed out.") from exc
    except requests.exceptions.ConnectionError as exc:
        raise ConnectionError(
            "Ollama is not running."
        ) from exc
    except Exception as exc:
        raise RuntimeError("Local AI failed. Using rule-based fallback where possible.") from exc


def _call_nvidia(messages: list[dict]) -> str:
    """
    Send messages to NVIDIA API using OpenAI-compatible format.

    POST to {NVIDIA_BASE_URL}/chat/completions with:
    - Headers: Authorization: Bearer {NVIDIA_API_KEY}, Content-Type: application/json
    - Body: {"model": model, "messages": messages, "temperature": 0}

    Raises ConnectionError if NVIDIA API is unreachable or key is invalid.
    """
    if requests is None:
        raise RuntimeError(
            "The 'requests' package is required for the NVIDIA backend. "
            "Run: pip install requests"
        )

    model = (os.getenv("NVIDIA_MODEL") or "nvidia/nemotron-3-ultra-550b-a55b").strip() or "nvidia/nemotron-3-ultra-550b-a55b"
    base_url = (os.getenv("NVIDIA_BASE_URL") or "https://integrate.api.nvidia.com/v1").strip()
    api_key = (os.getenv("NVIDIA_API_KEY") or "").strip()

    if not api_key:
        raise ValueError("NVIDIA_API_KEY is required for NVIDIA backend")

    # Ensure base_url doesn't end with slash to avoid double slashes
    base_url = base_url.rstrip("/")
    url = f"{base_url}/chat/completions"

    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=int(os.getenv("NVIDIA_TIMEOUT", "60")),  # configurable via NVIDIA_TIMEOUT env var
        )
        response.raise_for_status()
        data = response.json()

        # NVIDIA uses OpenAI-style response format
        if "choices" in data and len(data["choices"]) > 0:
            return data["choices"][0].get("message", {}).get("content", "")

        raise RuntimeError("NVIDIA API response did not contain generated SQL.")

    except requests.exceptions.ConnectionError as exc:
        raise ConnectionError(
            "NVIDIA API is unreachable. Please check your network connection and NVIDIA_BASE_URL."
        ) from exc
    except requests.exceptions.HTTPError as exc:
        if response.status_code == 401:
            raise ValueError("Invalid NVIDIA_API_KEY") from exc
        raise RuntimeError(f"NVIDIA API returned error: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"NVIDIA backend failed: {exc}") from exc


# ── Public entry point ────────────────────────────────────────────────────────

def _call_ai_backend(
    messages: list[dict],
    backend: str,
    response_format: dict | str | None = None,
) -> str:
    """Dispatch messages to the chosen backend and return the raw response."""
    if backend != "local":
        logger.info("Ignoring non-local AI backend; CLI workflow uses local Ollama only")
    return _call_ollama(messages, response_format=response_format)


def generate_sql(
    user_question: str,
    knowledge_base: dict,
    backend: str | None = None,
    query_plan: dict | None = None,
    selected_tables: list[dict] | None = None,
    business_glossary: dict | None = None,
    join_paths: list[dict] | None = None,
) -> str:
    """
    Generate a SQL SELECT statement for *user_question* using *knowledge_base*
    as context.

    Steps
    -----
    1. Build the prompt via build_sql_prompt().
    2. Send it to the local Ollama backend.
    3. Clean the raw response with extract_sql_only() to remove fences,
       preamble text, and trailing explanations.
    4. Return the cleaned SQL string.

    The caller is responsible for validating the returned SQL before storing or
    executing it. A LIMIT should only be added later when the user explicitly
    requested one and the SQL is structurally valid.

    Args:
        user_question:  Plain-English question from the user.
        knowledge_base: Dict loaded from semantic/knowledge_base.json.
        backend:        Ignored for the active CLI workflow; local is used.
        join_paths:     Computed join paths between selected tables.

    Returns:
        A cleaned SQL string (may still need limit injection / validation).

    Raises:
        ConnectionError:  Ollama server not running.
        RuntimeError:     Any other backend failure.
    """
    selected_backend = "local"

    messages = build_sql_prompt(
        user_question,
        knowledge_base,
        query_plan=query_plan,
        selected_tables=selected_tables,
        business_glossary=business_glossary,
        join_paths=join_paths,
    )
    raw_response = _call_ai_backend(messages, selected_backend)
    return _clean_sql_response(raw_response)


def generate_sql_with_retry(
    user_question: str,
    knowledge_base: dict,
    backend: str,
    first_attempt_sql: str,
    validation_reason: str,
    query_plan: dict | None = None,
    selected_tables: list[dict] | None = None,
    business_glossary: dict | None = None,
    validation_context: dict | None = None,
    join_paths: list[dict] | None = None,
) -> str:
    """
    Retry AI SQL generation once after a failed first attempt.

    Sends a correction prompt that includes:
    - The original question
    - The invalid SQL that was produced
    - The reason it was rejected
    - The schema context
    - Required tables and columns
    - Relationship/join paths
    - A strict instruction to return only executable SQL with no ellipsis or placeholder FROM

    Args:
        user_question:      Original user question.
        knowledge_base:     Knowledge base dict.
        backend:            Ignored for the active CLI workflow; local is used.
        first_attempt_sql:  The rejected SQL from the first attempt.
        validation_reason:  Why the first attempt was rejected.
        validation_context: Dynamic table, column, glossary, and relationship hints.
        join_paths:         Computed join paths between selected tables.

    Returns:
        Cleaned SQL string from the retry attempt.

    Raises:
        Same exceptions as generate_sql().
    """
    base_messages = build_sql_prompt(
        user_question,
        knowledge_base,
        query_plan=query_plan,
        selected_tables=selected_tables,
        business_glossary=business_glossary,
        join_paths=join_paths,
    )
    
    correction_system = (
        "You are correcting a previously invalid MySQL SELECT statement. "
        "Follow the structured plan, selected tables, selected columns, glossary, and relationships below exactly. "
        "Return ONLY one corrected executable SELECT statement. "
        "No explanation. No markdown. No comments. No extra text.\n\n"
        f"{base_messages[0]['content']}"
    )

    correction_user = (
        f"Original question: {user_question}\n\n"
        f"Rejected SQL:\n{first_attempt_sql}\n\n"
        f"Validation failure: {validation_reason}\n\n"
        f"Runtime schema and retrieval context:\n{validation_context or {}}\n\n"
        "Required tables: " + ", ".join([t.get('table', '') for t in (selected_tables or [])]) + "\n"
        "Required columns: " + ", ".join([f"{c.get('table', '')}.{c.get('column', '')}" for c in (validation_context.get('selected_columns', []) if validation_context else [])]) + "\n"
        "Relationship/join paths: " + str(join_paths or []) + "\n"
        "Join predicates to use: " + ", ".join(validation_context.get("join_conditions", []) if validation_context else []) + "\n\n"
        "Correct the SQL so it follows the plan, selected tables, selected relationships, glossary context, and safety rules. "
        "Use only allowed tables and columns from the schema context. "
        "Qualify columns with table aliases when more than one table is used. "
        "Output complete SQL only, with valid FROM and JOIN clauses, no ellipsis, no placeholder FROM, no incomplete JOIN."
    )

    messages = [
        {"role": "system", "content": correction_system},
        {"role": "user",   "content": correction_user},
    ]

    raw_response = _call_ai_backend(messages, "local")
    return _clean_sql_response(raw_response)
