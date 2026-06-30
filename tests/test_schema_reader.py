from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table, create_engine

from db.schema_reader import read_database_schema


def test_empty_database_returns_empty_dict():
    engine = create_engine("sqlite:///:memory:")

    assert read_database_schema(engine) == {}


def test_schema_reader_extracts_columns_primary_keys_and_foreign_keys():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table(
        "customers",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("name", String(50), nullable=False),
    )
    Table(
        "orders",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("customer_id", Integer, ForeignKey("customers.id")),
        Column("status", String(20)),
    )
    metadata.create_all(engine)

    schema = read_database_schema(engine)

    assert set(schema) == {"customers", "orders"}
    assert {"name": "id", "type": "INTEGER", "nullable": False} in schema["customers"]["columns"]
    assert schema["orders"]["primary_keys"] == ["id"]
    assert schema["orders"]["foreign_keys"] == [
        {
            "column": "customer_id",
            "referenced_table": "customers",
            "referenced_column": "id",
        }
    ]


def test_schema_reader_does_not_infer_relationships_without_constraints():
    engine = create_engine("sqlite:///:memory:")
    metadata = MetaData()
    Table(
        "customers",
        metadata,
        Column("customer_id", Integer, primary_key=True),
        Column("customer_name", String(50)),
    )
    Table(
        "orders",
        metadata,
        Column("order_id", Integer, primary_key=True),
        Column("customer_id", Integer),
    )
    Table(
        "products",
        metadata,
        Column("product_id", Integer, primary_key=True),
    )
    Table(
        "order_items",
        metadata,
        Column("order_item_id", Integer, primary_key=True),
        Column("order_id", Integer),
        Column("product_id", Integer),
    )
    Table(
        "payments",
        metadata,
        Column("payment_id", Integer, primary_key=True),
        Column("order_id", Integer),
    )
    Table(
        "support_tickets",
        metadata,
        Column("ticket_id", Integer, primary_key=True),
        Column("customer_id", Integer),
        Column("order_id", Integer),
    )
    metadata.create_all(engine)

    schema = read_database_schema(engine)

    assert all(not table_data["foreign_keys"] for table_data in schema.values())
