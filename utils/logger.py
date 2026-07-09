"""
utils/logger.py
================
Centralized logging configuration for the AI SQL Query Generator.

Features
--------
- Logs to logs/app.log with automatic rotation
- DEBUG_MODE env var controls verbose logging
- Never logs passwords or API keys
- Logs all key operations: app start, menu choices, DB connection, 
  KB generation, SQL generation, execution, charts, insights, errors

Usage
-----
    from utils.logger import get_logger
    logger = get_logger()
    logger.info("User chose option 1")
    logger.error("Database connection failed")
"""

import logging
import os
from pathlib import Path


def _get_log_level() -> int:
    """
    Determine log level from DEBUG_MODE environment variable.
    
    Returns:
        logging.DEBUG if DEBUG_MODE=true, otherwise logging.INFO
    """
    debug_mode = os.getenv("DEBUG_MODE", "false").strip().lower()
    return logging.DEBUG if debug_mode == "true" else logging.INFO


def get_logger(name: str = "aisqlqurrey") -> logging.Logger:
    """
    Get or create a logger with the specified name.
    
    The logger is configured once with:
    - File handler writing to logs/app.log
    - Console handler for immediate feedback
    - DEBUG or INFO level based on DEBUG_MODE env var
    - Format: timestamp - level - message
    
    Args:
        name: Logger name (default: "aisqlqurrey")
    
    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    
    # Avoid adding duplicate handlers if logger already configured
    if logger.handlers:
        return logger
    
    logger.setLevel(_get_log_level())
    
    # Format: timestamp - level - message
    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Console handler - keep normal CLI output clean. Detailed INFO logs still
    # go to logs/app.log; set DEBUG_MODE=true to also see them in the terminal.
    console_handler = logging.StreamHandler()
    console_level = (
        _get_log_level()
        if os.getenv("DEBUG_MODE", "false").strip().lower() == "true"
        else logging.WARNING
    )
    console_handler.setLevel(console_level)
    console_handler.setFormatter(formatter)

    # File handler - writes to logs/app.log when the file is available.
    # On Windows/OneDrive the log file can be temporarily locked, and logging
    # should never prevent the CLI or tests from starting.
    try:
        log_dir = Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(
            log_dir / "app.log",
            encoding="utf-8"
        )
        file_handler.setLevel(_get_log_level())
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError:
        pass

    logger.addHandler(console_handler)
    
    return logger


def log_sensitive_operation(operation: str, details: dict) -> None:
    """
    Log an operation while removing sensitive fields.
    
    This helper function removes passwords and API keys from the details
    dictionary before logging, ensuring sensitive data never reaches log files.
    
    Args:
        operation: Description of the operation (e.g., "Database connection")
        details: Dictionary containing operation details
    """
    logger = get_logger()
    
    # Create a safe copy of details
    safe_details = details.copy()
    
    # Remove sensitive fields
    sensitive_keys = ["password", "api_key", "secret", "token"]
    for key in list(safe_details.keys()):
        if any(sensitive in key.lower() for sensitive in sensitive_keys):
            safe_details[key] = "***REDACTED***"
    
    logger.info(f"{operation}: {safe_details}")
