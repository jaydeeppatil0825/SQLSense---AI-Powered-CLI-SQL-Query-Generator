"""
Phase 11: Basic Natural Language Testing
========================================
Tests basic natural language questions against the AI SQL Query Generator.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai.simple_query_generator import generate_simple_sql
from ai.sql_generator import generate_sql
from utils.sql_validator import validate_sql, add_limit_if_missing

# Knowledge base that mirrors ai_sales_demo structure
DEMO_KB = {
    "customers": {
        "columns": [
            {"name": "customer_id", "type": "INTEGER", "nullable": False},
            {"name": "customer_name", "type": "VARCHAR(100)", "nullable": False},
            {"name": "status", "type": "VARCHAR(20)", "nullable": True},
            {"name": "signup_date", "type": "DATE", "nullable": True},
        ],
        "primary_keys": ["customer_id"],
        "foreign_keys": [],
    },
    "orders": {
        "columns": [
            {"name": "order_id", "type": "INTEGER", "nullable": False},
            {"name": "customer_id", "type": "INTEGER", "nullable": False},
            {"name": "order_date", "type": "DATE", "nullable": False},
            {"name": "order_status", "type": "VARCHAR(30)", "nullable": True},
            {"name": "payment_status", "type": "VARCHAR(30)", "nullable": True},
            {"name": "final_amount", "type": "DECIMAL(12,2)", "nullable": True},
            {"name": "total_amount", "type": "DECIMAL(12,2)", "nullable": True},
        ],
        "primary_keys": ["order_id"],
        "foreign_keys": [{"column": "customer_id", "referenced_table": "customers", "referenced_column": "customer_id"}],
    },
    "payments": {
        "columns": [
            {"name": "payment_id", "type": "INTEGER", "nullable": False},
            {"name": "order_id", "type": "INTEGER", "nullable": False},
            {"name": "paid_amount", "type": "DECIMAL(12,2)", "nullable": True},
            {"name": "payment_status", "type": "VARCHAR(30)", "nullable": True},
            {"name": "payment_date", "type": "DATE", "nullable": True},
        ],
        "primary_keys": ["payment_id"],
        "foreign_keys": [],
    },
    "employees": {
        "columns": [
            {"name": "employee_id", "type": "INTEGER", "nullable": False},
            {"name": "employee_name", "type": "VARCHAR(100)", "nullable": True},
            {"name": "salary", "type": "DECIMAL(12,2)", "nullable": True},
            {"name": "joining_date", "type": "DATE", "nullable": True},
        ],
        "primary_keys": ["employee_id"],
        "foreign_keys": [],
    },
    "products": {
        "columns": [
            {"name": "product_id", "type": "INTEGER", "nullable": False},
            {"name": "product_name", "type": "VARCHAR(100)", "nullable": False},
            {"name": "unit_price", "type": "DECIMAL(10,2)", "nullable": False},
        ],
        "primary_keys": ["product_id"],
        "foreign_keys": [],
    },
    "support_tickets": {
        "columns": [
            {"name": "ticket_id", "type": "INTEGER", "nullable": False},
            {"name": "customer_id", "type": "INTEGER", "nullable": False},
            {"name": "subject", "type": "VARCHAR(200)", "nullable": True},
            {"name": "status", "type": "VARCHAR(30)", "nullable": True},
            {"name": "created_at", "type": "DATE", "nullable": True},
        ],
        "primary_keys": ["ticket_id"],
        "foreign_keys": [],
    },
}

# Test questions
TEST_QUESTIONS = [
    "Can you show me all customers?",
    "Can you show me all products?",
    "Can you show me all orders?",
    "Can you show me all payments?",
    "Can you show me all employees?",
    "Can you show me all support tickets?",
    "How many customers do we have?",
    "How many products are available?",
    "How many orders are there?",
    "How many payments are recorded?",
    "What is our total sales amount?",
    "How much money has been paid?",
    "Which payments are still pending?",
    "Which customers are active?",
    "Can you show me the latest orders?",
]

def evaluate_question(question: str, kb: dict) -> dict:
    """Test a single question and return results. NOTE: This is not a pytest test - run via main()"""
    result = {
        "question": question,
        "passed": False,
        "generated_sql": None,
        "sql_validation_passed": False,
        "validation_reason": None,
        "method_used": None,
        "error": None,
        "correct_table": None,
        "correct_column": None,
    }
    
    # Try simple query generator first
    try:
        simple_sql = generate_simple_sql(question, kb)
        if simple_sql:
            result["method_used"] = "simple"
            result["generated_sql"] = simple_sql
            
            # Validate SQL
            is_valid, reason = validate_sql(simple_sql)
            result["sql_validation_passed"] = is_valid
            result["validation_reason"] = reason
            
            if is_valid:
                result["passed"] = True
                # Check if correct table is selected
                if "customers" in question.lower() and "customers" in simple_sql.lower():
                    result["correct_table"] = True
                elif "products" in question.lower() and "products" in simple_sql.lower():
                    result["correct_table"] = True
                elif "orders" in question.lower() and "orders" in simple_sql.lower():
                    result["correct_table"] = True
                elif "payments" in question.lower() and "payments" in simple_sql.lower():
                    result["correct_table"] = True
                elif "employees" in question.lower() and "employees" in simple_sql.lower():
                    result["correct_table"] = True
                elif "support tickets" in question.lower() and "support_tickets" in simple_sql.lower():
                    result["correct_table"] = True
                else:
                    result["correct_table"] = False
            return result
    except Exception as e:
        result["error"] = f"Simple generator error: {str(e)}"
    
    # Try AI generator if simple failed
    try:
        ai_sql = generate_sql(question, kb, backend="local")
        if ai_sql:
            result["method_used"] = "ai"
            result["generated_sql"] = ai_sql
            
            # Validate SQL
            is_valid, reason = validate_sql(ai_sql)
            result["sql_validation_passed"] = is_valid
            result["validation_reason"] = reason
            
            if is_valid:
                result["passed"] = True
                # Check if correct table is selected
                if "customers" in question.lower() and "customers" in ai_sql.lower():
                    result["correct_table"] = True
                elif "products" in question.lower() and "products" in ai_sql.lower():
                    result["correct_table"] = True
                elif "orders" in question.lower() and "orders" in ai_sql.lower():
                    result["correct_table"] = True
                elif "payments" in question.lower() and "payments" in ai_sql.lower():
                    result["correct_table"] = True
                elif "employees" in question.lower() and "employees" in ai_sql.lower():
                    result["correct_table"] = True
                elif "support tickets" in question.lower() and "support_tickets" in ai_sql.lower():
                    result["correct_table"] = True
                else:
                    result["correct_table"] = False
            return result
    except Exception as e:
        result["error"] = f"AI generator error: {str(e)}"
    
    return result

def main():
    """Run all test questions and generate report."""
    print("=" * 80)
    print("Phase 11: Basic Natural Language Testing")
    print("=" * 80)
    print()
    
    results = []
    
    for question in TEST_QUESTIONS:
        print(f"Testing: {question}")
        result = evaluate_question(question, DEMO_KB)
        results.append(result)
        
        if result["passed"]:
            print(f"  [PASSED]")
            print(f"  Method: {result['method_used']}")
            print(f"  SQL: {result['generated_sql']}")
            print(f"  Validation: {'PASSED' if result['sql_validation_passed'] else 'FAILED'}")
            print(f"  Correct table: {'YES' if result['correct_table'] else 'NO'}")
        else:
            print(f"  [FAILED]")
            print(f"  Error: {result['error']}")
        print()
    
    # Generate summary report
    print("=" * 80)
    print("SUMMARY REPORT")
    print("=" * 80)
    print()
    
    passed = sum(1 for r in results if r["passed"])
    failed = len(results) - passed
    
    print(f"Total questions: {len(results)}")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print()
    
    print("=" * 80)
    print("DETAILED RESULTS")
    print("=" * 80)
    print()
    
    for i, result in enumerate(results, 1):
        print(f"{i}. {result['question']}")
        print(f"   Status: {'PASSED' if result['passed'] else 'FAILED'}")
        print(f"   Method: {result['method_used']}")
        print(f"   Generated SQL: {result['generated_sql']}")
        print(f"   SQL Validation: {'PASSED' if result['sql_validation_passed'] else 'FAILED'}")
        if result['validation_reason']:
            print(f"   Validation Reason: {result['validation_reason']}")
        print(f"   Correct Table: {'YES' if result['correct_table'] else 'NO'}")
        if result['error']:
            print(f"   Error: {result['error']}")
        print()
    
    # Save results to file
    import json
    with open("phase11_test_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    
    print("Results saved to phase11_test_results.json")

if __name__ == "__main__":
    main()
