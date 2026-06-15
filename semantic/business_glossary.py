"""
semantic/business_glossary.py
==============================
Generate and search a business glossary from the knowledge base.

The business glossary maps plain-English business terms to actual
database tables and columns, making it easier for users to ask questions
in natural language.

Features
--------
- Generates glossary from enriched knowledge base
- Maps business terms to table/columns with confidence scores
- Provides example questions for each term
- Search functionality to find relevant glossary entries
- Fallback to rule-based glossary if AI enrichment is not available
"""

from typing import Dict, List, Any

from utils.file_utils import save_json
from utils.logger import get_logger

logger = get_logger()


# Rule-based business term mappings (fallback when AI enrichment is not available)
_RULE_BASED_MAPPINGS = {
    "sales": {
        "description": "Total revenue or order amount.",
        "column_patterns": ["final_amount", "total_amount", "amount", "sales"],
        "preferred_columns": [("orders", "final_amount"), ("orders", "total_amount")],
        "example_questions": [
            "Show total sales",
            "Show monthly sales",
            "Show sales by city"
        ]
    },
    "revenue": {
        "description": "Income generated from sales.",
        "column_patterns": ["final_amount", "total_amount", "revenue", "income"],
        "preferred_columns": [("orders", "final_amount"), ("orders", "total_amount")],
        "example_questions": [
            "Show total revenue",
            "Show revenue by product",
            "Show monthly revenue"
        ]
    },
    "order value": {
        "description": "The monetary value of an order.",
        "column_patterns": ["final_amount", "total_amount", "order_value"],
        "preferred_columns": [("orders", "final_amount"), ("orders", "total_amount")],
        "example_questions": [
            "Show average order value",
            "Show total order value"
        ]
    },
    "paid amount": {
        "description": "Amount actually paid for an order.",
        "column_patterns": ["paid_amount", "payment_amount"],
        "example_questions": [
            "Show total paid amount",
            "Show paid amount by customer"
        ]
    },
    "pending payment": {
        "description": "Payments that are not yet completed.",
        "column_patterns": ["payment_status", "status"],
        "preferred_columns": [("payments", "payment_status"), ("orders", "payment_status")],
        "example_questions": [
            "Show pending payments",
            "Show orders with pending payment"
        ]
    },
    "customer": {
        "description": "Person or organization placing orders.",
        "column_patterns": ["customer_name", "customer_id", "name"],
        "table_patterns": ["customers"],
        "example_questions": [
            "Show all customers",
            "Show top customers by sales",
            "Show customer count"
        ]
    },
    "product": {
        "description": "Items sold by the business.",
        "column_patterns": ["product_name", "product_id"],
        "table_patterns": ["products"],
        "example_questions": [
            "Show all products",
            "Show products by category",
            "Show product count"
        ]
    },
    "quantity": {
        "description": "Number of items ordered or in stock.",
        "column_patterns": ["quantity", "qty", "units", "stock"],
        "preferred_columns": [("order_items", "quantity"), ("products", "stock_quantity")],
        "example_questions": [
            "Show total quantity sold",
            "Show products with low quantity"
        ]
    },
    "city": {
        "description": "Geographic location for customers or shipping.",
        "column_patterns": ["city", "location", "shipping_city"],
        "preferred_columns": [("customers", "city"), ("orders", "shipping_city")],
        "example_questions": [
            "Show sales by city",
            "Show customers in each city"
        ]
    },
    "category": {
        "description": "Classification or grouping of products.",
        "column_patterns": ["category", "type", "group"],
        "preferred_columns": [("products", "category")],
        "example_questions": [
            "Show products by category",
            "Show sales by category"
        ]
    },
    "status": {
        "description": "Current state of an order or payment.",
        "column_patterns": ["status", "state", "order_status", "payment_status"],
        "example_questions": [
            "Show orders by status",
            "Show pending orders"
        ]
    },
    "monthly": {
        "description": "Time-based grouping by month.",
        "column_patterns": ["order_date", "date", "created_at"],
        "preferred_columns": [("orders", "order_date")],
        "example_questions": [
            "Show monthly sales",
            "Show monthly revenue"
        ]
    },
    "date": {
        "description": "Temporal information for events.",
        "column_patterns": ["order_date", "date", "created_at", "updated_at"],
        "example_questions": [
            "Show orders by date",
            "Show recent orders"
        ]
    },
    "employee": {
        "description": "Staff members working for the business.",
        "column_patterns": ["employee_name", "employee_id", "name"],
        "table_patterns": ["employees"],
        "example_questions": [
            "Show all employees",
            "Show employees by department"
        ]
    },
    "salary": {
        "description": "Compensation paid to employees.",
        "column_patterns": ["salary", "wage", "compensation"],
        "example_questions": [
            "Show average salary",
            "Show total salary by department"
        ]
    },
    "support ticket": {
        "description": "Customer service requests or issues.",
        "column_patterns": ["ticket_id", "subject", "issue"],
        "table_patterns": ["support_tickets", "tickets"],
        "example_questions": [
            "Show open support tickets",
            "Show tickets by status"
        ]
    },
    "orders": {
        "description": "Customer purchases or order transactions.",
        "column_patterns": ["order_id", "order_date", "order_status", "payment_status", "final_amount"],
        "table_patterns": ["orders"],
        "example_questions": [
            "Show all orders",
            "Show orders by status",
            "Show high value orders"
        ]
    },
    "payments": {
        "description": "Payment records linked to orders.",
        "column_patterns": ["payment_id", "payment_status", "paid_amount", "payment_method"],
        "table_patterns": ["payments"],
        "example_questions": [
            "Show pending payments",
            "Show payment details with customer names"
        ]
    },
    "paid": {
        "description": "Completed payments or paid orders.",
        "column_patterns": ["payment_status"],
        "preferred_columns": [("payments", "payment_status"), ("orders", "payment_status")],
        "example_questions": [
            "Show paid orders",
            "Show paid payments"
        ]
    },
    "top customers": {
        "description": "Customers ranked by total sales value.",
        "column_patterns": ["customer_id", "customer_name", "final_amount"],
        "preferred_columns": [("customers", "customer_name"), ("orders", "final_amount")],
        "example_questions": [
            "Show top 5 customers by sales",
            "Show top customers by revenue"
        ]
    },
    "monthly sales": {
        "description": "Sales grouped by order month.",
        "column_patterns": ["order_date", "final_amount", "total_amount"],
        "preferred_columns": [("orders", "order_date"), ("orders", "final_amount")],
        "example_questions": [
            "Show monthly sales",
            "Show revenue by month"
        ]
    },
    "high value orders": {
        "description": "Orders above a requested value threshold.",
        "column_patterns": ["final_amount", "total_amount"],
        "preferred_columns": [("orders", "final_amount"), ("orders", "total_amount")],
        "example_questions": [
            "Show high value orders above 50000"
        ]
    },
    "customer type": {
        "description": "Customer segment such as Enterprise, Retail, or Wholesale.",
        "column_patterns": ["customer_type"],
        "preferred_columns": [("customers", "customer_type")],
        "example_questions": [
            "Show sales by customer type"
        ]
    }
}

_ERP_RULE_BASED_MAPPINGS = {
    "purchase": {
        "description": "Purchase orders, vendor bills, or procurement amounts.",
        "column_patterns": ["purchase_amount", "final_amount", "total_amount", "amount"],
        "table_patterns": ["purchases", "purchase_orders", "vendor_invoices"],
        "example_questions": [
            "Show purchase by vendor",
            "Show total purchase this month",
        ],
    },
    "vendor": {
        "description": "Suppliers or vendors the business buys from.",
        "column_patterns": ["vendor_name", "vendor_id", "supplier_name", "supplier_id", "name"],
        "table_patterns": ["vendors", "suppliers"],
        "example_questions": [
            "Show purchase by vendor",
            "Show vendor pending payments",
        ],
    },
    "invoice": {
        "description": "Sales or purchase billing documents.",
        "column_patterns": ["invoice_no", "invoice_number", "invoice_date", "invoice_amount", "amount_due"],
        "table_patterns": ["invoices", "sales_invoices", "vendor_invoices"],
        "example_questions": [
            "Show unpaid invoices",
            "Show invoice amount by month",
        ],
    },
    "payment": {
        "description": "Money received from customers or paid to vendors.",
        "column_patterns": ["payment_status", "paid_amount", "payment_amount", "payment_date", "amount_due"],
        "table_patterns": ["payments", "vendor_payments", "customer_payments"],
        "example_questions": [
            "Show vendor pending payments",
            "Show payments by month",
        ],
    },
    "inventory": {
        "description": "Current stock position and inventory movement.",
        "column_patterns": ["stock_qty", "quantity", "available_stock", "on_hand", "warehouse_id"],
        "table_patterns": ["inventory", "inventory_balance", "stock_ledger"],
        "example_questions": [
            "Show current stock by warehouse",
            "Show low stock items",
        ],
    },
    "warehouse": {
        "description": "Storage locations for stock and materials.",
        "column_patterns": ["warehouse_name", "warehouse_code", "warehouse_id", "location"],
        "table_patterns": ["warehouses"],
        "example_questions": [
            "Show current stock by warehouse",
            "Show warehouse wise stock",
        ],
    },
    "ledger": {
        "description": "Accounting ledger or balance tracking data.",
        "column_patterns": ["ledger_name", "ledger_code", "ledger_id", "balance", "debit", "credit"],
        "table_patterns": ["ledgers", "general_ledger", "gl_entries"],
        "example_questions": [
            "Show customer outstanding balance",
            "Show ledger balance",
        ],
    },
    "account": {
        "description": "Chart of account or finance account definitions.",
        "column_patterns": ["account_name", "account_code", "account_id", "balance"],
        "table_patterns": ["accounts", "chart_of_accounts"],
        "example_questions": [
            "Show balance by account",
            "Show tax account balances",
        ],
    },
    "tax": {
        "description": "Tax amounts such as GST or VAT charged on transactions.",
        "column_patterns": ["tax_amount", "gst_amount", "vat_amount", "taxable_amount"],
        "table_patterns": ["taxes", "invoices", "orders"],
        "example_questions": [
            "Show tax collected by month",
            "Show GST by invoice",
        ],
    },
    "gst": {
        "description": "Goods and Services Tax charged on invoices or orders.",
        "column_patterns": ["gst_amount", "gst_rate", "tax_amount"],
        "table_patterns": ["invoices", "orders", "taxes"],
        "example_questions": [
            "Show GST collected by month",
            "Show GST by customer",
        ],
    },
    "salary": {
        "description": "Employee compensation and payroll totals.",
        "column_patterns": ["salary", "gross_salary", "net_salary", "pay_amount"],
        "table_patterns": ["employees", "payroll", "salary_register"],
        "example_questions": [
            "Show salary by department",
            "Show total salary this month",
        ],
    },
    "department": {
        "description": "Business unit or department grouping for employees.",
        "column_patterns": ["department", "department_name", "department_id"],
        "table_patterns": ["departments", "employees"],
        "example_questions": [
            "Show salary by department",
            "Show employees by department",
        ],
    },
    "production": {
        "description": "Manufacturing or production execution data.",
        "column_patterns": ["production_qty", "produced_qty", "work_order_no", "bom_id"],
        "table_patterns": ["production_orders", "production_entries", "work_orders"],
        "example_questions": [
            "Show production by BOM",
            "Show produced quantity by date",
        ],
    },
    "bom": {
        "description": "Bill of materials linking products to required materials.",
        "column_patterns": ["bom_no", "bom_id", "material_id", "required_qty"],
        "table_patterns": ["bom", "bom_items", "bill_of_materials"],
        "example_questions": [
            "Show BOM materials",
            "Show production by BOM",
        ],
    },
    "material": {
        "description": "Raw materials or components consumed in production.",
        "column_patterns": ["material_name", "material_id", "required_qty", "consumed_qty"],
        "table_patterns": ["materials", "bom_items", "material_issue"],
        "example_questions": [
            "Show required material quantity by BOM",
            "Show material consumption",
        ],
    },
    "balance": {
        "description": "Open balance or outstanding monetary amount.",
        "column_patterns": ["balance", "outstanding_amount", "amount_due", "pending_amount"],
        "example_questions": [
            "Show customer outstanding balance",
            "Show vendor balance due",
        ],
    },
    "due": {
        "description": "Amounts that are unpaid, pending, or due.",
        "column_patterns": ["due_date", "amount_due", "balance_due", "pending_amount"],
        "example_questions": [
            "Show unpaid invoices",
            "Show vendor payments due",
        ],
    },
    "pending": {
        "description": "Transactions or documents not yet completed.",
        "column_patterns": ["status", "payment_status", "invoice_status", "pending_amount"],
        "example_questions": [
            "Show vendor pending payments",
            "Show pending invoices",
        ],
    },
}


def _combined_rule_mappings() -> Dict[str, Any]:
    combined = dict(_RULE_BASED_MAPPINGS)
    combined.update(_ERP_RULE_BASED_MAPPINGS)
    return combined


def get_default_business_glossary() -> Dict[str, Any]:
    """
    Return a hardcoded glossary fallback that works without a JSON file.
    """
    glossary = {}
    for term, term_data in _combined_rule_mappings().items():
        mapped_columns = []
        for table_name, column_name in term_data.get("preferred_columns", []):
            mapped_columns.append(
                {
                    "table": table_name,
                    "column": column_name,
                    "type": "unknown",
                    "confidence": "medium",
                }
            )
        glossary[term] = {
            "description": term_data["description"],
            "mapped_columns": mapped_columns,
            "example_questions": term_data.get("example_questions", []),
        }
    return glossary


def _find_columns_for_term(knowledge_base: dict, term_data: dict) -> List[Dict[str, Any]]:
    """
    Find columns in the knowledge base that match a business term.
    
    Args:
        knowledge_base: The knowledge base dict
        term_data: The term data with column_patterns and table_patterns
    
    Returns:
        List of matching column mappings with confidence scores
    """
    mappings = []
    column_patterns = term_data.get("column_patterns", [])
    table_patterns = term_data.get("table_patterns", [])
    preferred_columns = term_data.get("preferred_columns", [])
    seen = set()

    for preferred_table, preferred_column in preferred_columns:
        table_data = knowledge_base.get(preferred_table)
        if not table_data:
            continue
        for col in table_data.get("columns", []):
            if col.get("name") != preferred_column:
                continue
            key = (preferred_table, preferred_column)
            seen.add(key)
            mappings.append({
                "table": preferred_table,
                "column": preferred_column,
                "type": col.get("type", ""),
                "confidence": "high",
            })
    
    for table_name, table_data in knowledge_base.items():
        # Check if table matches table patterns
        table_match = any(pattern.lower() in table_name.lower() for pattern in table_patterns)
        
        for col in table_data.get("columns", []):
            col_name = col.get("name", "")
            col_type = col.get("type", "")
            
            # Check if column matches column patterns
            col_match = any(pattern.lower() in col_name.lower() for pattern in column_patterns)
            
            # Determine confidence
            confidence = "low"
            if table_match and col_match:
                confidence = "high"
            elif col_match:
                confidence = "medium"
            
            if col_match:
                key = (table_name, col_name)
                if key in seen:
                    continue
                seen.add(key)
                mappings.append({
                    "table": table_name,
                    "column": col_name,
                    "type": col_type,
                    "confidence": confidence
                })
    
    return mappings


def generate_business_glossary(knowledge_base: dict, use_ai_enrichment: bool = False) -> Dict[str, Any]:
    """
    Generate a business glossary from the knowledge base.
    
    If AI enrichment is available (business_description, business_terms in columns),
    use those to build the glossary. Otherwise, fall back to rule-based mappings.
    
    Args:
        knowledge_base: The knowledge base dict (may be AI-enriched)
        use_ai_enrichment: Whether to use AI enrichment data if available
    
    Returns:
        Business glossary dict
    """
    logger.info("Generating business glossary")
    
    glossary = {}
    
    # Check if knowledge base has AI enrichment
    has_ai_enrichment = False
    for table_data in knowledge_base.values():
        for col in table_data.get("columns", []):
            if "business_terms" in col and col["business_terms"]:
                has_ai_enrichment = True
                break
        if has_ai_enrichment:
            break
    
    if use_ai_enrichment and has_ai_enrichment:
        # Build glossary from AI enrichment data
        logger.info("Using AI enrichment data for glossary generation")
        
        for table_name, table_data in knowledge_base.items():
            for col in table_data.get("columns", []):
                business_terms = col.get("business_terms", [])
                business_description = col.get("business_description", "")
                
                for term in business_terms:
                    term_lower = term.lower()
                    
                    if term_lower not in glossary:
                        glossary[term_lower] = {
                            "description": business_description or f"Business term: {term}",
                            "mapped_columns": [],
                            "example_questions": []
                        }
                    
                    # Add column mapping
                    col_type = col.get("type", "")
                    metric_type = col.get("metric_type", "general")
                    
                    glossary[term_lower]["mapped_columns"].append({
                        "table": table_name,
                        "column": col.get("name", ""),
                        "type": col_type,
                        "metric_type": metric_type,
                        "confidence": "high"  # AI enrichment is high confidence
                    })
        
        # Add example questions from table-level enrichment
        for table_name, table_data in knowledge_base.items():
            possible_questions = table_data.get("possible_business_questions", [])
            for question in possible_questions:
                # Try to match question to existing terms
                question_lower = question.lower()
                for term in glossary:
                    if term in question_lower:
                        if question not in glossary[term]["example_questions"]:
                            glossary[term]["example_questions"].append(question)
    
    else:
        # Fall back to rule-based mappings
        logger.info("Using rule-based mappings for glossary generation")
        
        for term, term_data in _combined_rule_mappings().items():
            mappings = _find_columns_for_term(knowledge_base, term_data)
            
            if mappings:
                glossary[term] = {
                    "description": term_data["description"],
                    "mapped_columns": mappings,
                    "example_questions": term_data["example_questions"]
                }
    
    logger.info(f"Generated glossary with {len(glossary)} terms")
    return glossary


def save_business_glossary(glossary: Dict[str, Any], output_path: str = "semantic/business_glossary.json") -> None:
    """
    Save the business glossary to a JSON file.
    
    Args:
        glossary: The glossary dict
        output_path: Path to save the glossary
    """
    try:
        save_json(glossary, output_path)
        logger.info(f"Business glossary saved to {output_path}")
    except Exception as exc:
        logger.error(f"Failed to save business glossary: {exc}")
        raise


def load_business_glossary(glossary_path: str = "semantic/business_glossary.json") -> Dict[str, Any]:
    """
    Load the business glossary from a JSON file.
    
    Args:
        glossary_path: Path to the glossary file
    
    Returns:
        Glossary dict loaded from disk, or a hardcoded fallback glossary when the
        file is missing, invalid, or unreadable.
    """
    from utils.file_utils import load_json
    
    try:
        glossary = load_json(glossary_path)
        logger.info(f"Business glossary loaded from {glossary_path}")
        return glossary if isinstance(glossary, dict) and glossary else get_default_business_glossary()
    except FileNotFoundError:
        logger.warning(f"Business glossary not found at {glossary_path}")
        return get_default_business_glossary()
    except ValueError as exc:
        logger.error(f"Business glossary at {glossary_path} is invalid: {exc}")
        return get_default_business_glossary()
    except OSError as exc:
        logger.error(f"Business glossary at {glossary_path} is unreadable: {exc}")
        return get_default_business_glossary()
    except Exception as exc:
        logger.error(f"Failed to load business glossary: {exc}")
        return get_default_business_glossary()


def search_business_glossary(search_term: str, glossary: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """
    Search the business glossary for a term.
    
    Searches across:
    - Glossary term names
    - Descriptions
    - Mapped table names
    - Mapped column names
    - Example questions
    
    Args:
        search_term: The term to search for
        glossary: The glossary dict (loads from file if not provided)
    
    Returns:
        Dict of matching glossary entries
    """
    if glossary is None:
        glossary = load_business_glossary()
    
    if not glossary:
        logger.warning("Business glossary is empty or not loaded")
        return {}
    
    search_lower = search_term.lower()
    matches = {}
    
    for term, term_data in glossary.items():
        try:
            # Search in term name
            if search_lower in str(term):
                matches[term] = term_data
                continue
            
            # Search in description
            description = term_data.get("description", "")
            if isinstance(description, str):
                description = description.lower()
                if search_lower in description:
                    matches[term] = term_data
                    continue
            
            # Search in mapped columns
            for mapping in term_data.get("mapped_columns", []):
                table = mapping.get("table", "")
                if isinstance(table, str) and search_lower in table.lower():
                    matches[term] = term_data
                    break
                column = mapping.get("column", "")
                if isinstance(column, str) and search_lower in column.lower():
                    matches[term] = term_data
                    break
            
            # Search in example questions
            for question in term_data.get("example_questions", []):
                if isinstance(question, str) and search_lower in question.lower():
                    matches[term] = term_data
                    break
        except Exception as e:
            logger.warning(f"Error processing term '{term}': {e}")
            continue
    
    logger.info(f"Glossary search for '{search_term}' found {len(matches)} matches")
    return matches
