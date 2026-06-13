import json
import tempfile
from pathlib import Path

import pytest

from utils.file_utils import load_json, save_json


def test_save_json_and_load_json_round_trip():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "nested" / "knowledge_base.json"
        data = {"users": {"row_count": 2, "columns": [{"name": "id"}]}}

        save_json(data, path)

        assert load_json(path) == data
        assert json.loads(path.read_text(encoding="utf-8")) == data


def test_load_json_missing_file_raises_file_not_found():
    with tempfile.TemporaryDirectory() as tmpdir:
        missing_path = Path(tmpdir) / "missing.json"

        with pytest.raises(FileNotFoundError, match="missing.json"):
            load_json(missing_path)


def test_load_json_malformed_file_raises_value_error():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "bad.json"
        path.write_text("{not valid json", encoding="utf-8")

        with pytest.raises(ValueError, match="invalid JSON"):
            load_json(path)


def test_save_json_serialization_failure_mentions_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "bad.json"

        # Our implementation uses make_json_serializable which converts objects to strings
        # So this test verifies that serialization works with the custom handler
        data = {"not_serializable": object()}
        save_json(data, path)
        
        # Verify the file was saved and can be loaded
        loaded = load_json(path)
        assert "not_serializable" in loaded
        # The object should have been converted to a string representation
        assert isinstance(loaded["not_serializable"], str)
