from sqlalchemy import create_engine, text

from db.data_profiler import profile_database_data


def test_empty_schema_data_returns_empty_dict():
    engine = create_engine("sqlite:///:memory:")

    assert profile_database_data({}, engine) == {}


def test_profiles_row_counts_column_counts_samples_and_min_max():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, age INTEGER)"))
        connection.execute(
            text("INSERT INTO users (name, age) VALUES (:name, :age)"),
            [{"name": "Asha", "age": 30}, {"name": "Ben", "age": None}, {"name": "Asha", "age": 40}],
        )

    schema = {
        "users": {
            "columns": [
                {"name": "name", "type": "TEXT", "nullable": True},
                {"name": "age", "type": "INTEGER", "nullable": True},
            ],
            "primary_keys": ["id"],
            "foreign_keys": [],
        }
    }

    profiled = profile_database_data(schema, engine)
    columns = {column["name"]: column for column in profiled["users"]["columns"]}

    assert profiled["users"]["row_count"] == 3
    assert columns["name"]["null_count"] == 0
    assert columns["name"]["non_null_count"] == 3
    assert columns["name"]["unique_count"] == 2
    assert set(columns["name"]["sample_values"]) == {"Asha", "Ben"}
    assert columns["age"]["min_value"] == 30
    assert columns["age"]["max_value"] == 40


def test_records_row_count_error_and_column_profile_error():
    engine = create_engine("sqlite:///:memory:")
    schema = {
        "missing_table": {
            "columns": [{"name": "missing_column", "type": "INTEGER", "nullable": True}],
            "primary_keys": [],
            "foreign_keys": [],
        }
    }

    profiled = profile_database_data(schema, engine)

    assert "row_count_error" in profiled["missing_table"]
    assert "profile_error" in profiled["missing_table"]["columns"][0]
