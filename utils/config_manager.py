"""
Local connection configuration manager for SQLSense.
Stores last-used database connection details (excluding passwords).
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


def _get_config_dir() -> Path:
    """Get the local config directory path."""
    return Path(".sqlsense")


def _get_config_file() -> Path:
    """Get the connection config file path."""
    return _get_config_dir() / "local_connection.json"


def save_connection_config(
    host: str,
    port: int,
    username: str,
    database: str,
    db_type: str = "mysql"
) -> None:
    """
    Save connection configuration to local file.
    Does not store password for security.
    
    Args:
        host: Database host
        port: Database port
        username: Database username
        database: Database name
        db_type: Database type (mysql, postgres, etc.)
    """
    config_dir = _get_config_dir()
    config_dir.mkdir(exist_ok=True)
    
    config = {
        "host": host,
        "port": port,
        "username": username,
        "database": database,
        "db_type": db_type,
    }
    
    config_file = _get_config_file()
    with open(config_file, "w") as f:
        json.dump(config, f, indent=2)


def load_connection_config() -> Optional[Dict[str, Any]]:
    """
    Load last-used connection configuration.
    
    Returns:
        Dict with connection details or None if no config exists
    """
    config_file = _get_config_file()
    if not config_file.exists():
        return None
    
    try:
        with open(config_file, "r") as f:
            return json.load(f)
    except Exception:
        return None


def has_saved_connection() -> bool:
    """Check if a saved connection configuration exists."""
    return _get_config_file().exists()


def clear_connection_config() -> None:
    """Remove saved connection configuration."""
    config_file = _get_config_file()
    if config_file.exists():
        config_file.unlink()


def format_connection_summary(config: Dict[str, Any]) -> str:
    """
    Format connection config as a readable summary string.
    
    Args:
        config: Connection configuration dict
        
    Returns:
        Formatted connection summary string
    """
    if not config:
        return "No connection"
    
    host = config.get("host", "unknown")
    port = config.get("port", "unknown")
    database = config.get("database", "unknown")
    username = config.get("username", "unknown")
    db_type = config.get("db_type", "mysql")
    
    return f"{db_type}://{username}@{host}:{port}/{database}"
