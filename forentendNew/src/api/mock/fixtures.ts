import type {
  GlossaryEntry,
  KnowledgeBaseStatus,
  QueryResponse,
  RelationshipGraph,
  SchemaTable,
} from "../types";

export const MOCK_TABLES: SchemaTable[] = [
  {
    name: "customers",
    type: "table",
    description: "Registered customer accounts.",
    columnCount: 8,
    columns: [
      {
        name: "id",
        dataType: "bigint",
        nullable: false,
        primaryKey: true,
        isNumeric: true,
        plannerRole: "identifier",
      },
      {
        name: "name",
        dataType: "varchar(120)",
        nullable: false,
        plannerRole: "dimension",
        samples: ["Ada Lovelace", "Ravi Kumar"],
      },
      {
        name: "email",
        dataType: "varchar(160)",
        nullable: false,
        unique: true,
        plannerRole: "identifier",
      },
      {
        name: "city",
        dataType: "varchar(80)",
        nullable: true,
        plannerRole: "dimension",
        samples: ["Pune", "Lisbon"],
      },
      { name: "country", dataType: "varchar(80)", nullable: true, plannerRole: "dimension" },
      {
        name: "status",
        dataType: "varchar(16)",
        nullable: false,
        plannerRole: "status",
        samples: ["active", "inactive"],
      },
      {
        name: "created_at",
        dataType: "timestamp",
        nullable: false,
        isDate: true,
        plannerRole: "date",
      },
      {
        name: "updated_at",
        dataType: "timestamp",
        nullable: true,
        isDate: true,
        plannerRole: "date",
      },
    ],
  },
  {
    name: "orders",
    type: "table",
    description: "Customer orders.",
    columnCount: 7,
    columns: [
      {
        name: "id",
        dataType: "bigint",
        nullable: false,
        primaryKey: true,
        isNumeric: true,
        plannerRole: "identifier",
      },
      {
        name: "customer_id",
        dataType: "bigint",
        nullable: false,
        foreignKey: { table: "customers", column: "id" },
        plannerRole: "identifier",
      },
      { name: "status", dataType: "varchar(16)", nullable: false, plannerRole: "status" },
      {
        name: "total_amount",
        dataType: "numeric(12,2)",
        nullable: false,
        isNumeric: true,
        plannerRole: "metric",
      },
      { name: "currency", dataType: "char(3)", nullable: false, plannerRole: "dimension" },
      {
        name: "placed_at",
        dataType: "timestamp",
        nullable: false,
        isDate: true,
        plannerRole: "date",
      },
      {
        name: "shipped_at",
        dataType: "timestamp",
        nullable: true,
        isDate: true,
        plannerRole: "date",
      },
    ],
  },
  {
    name: "order_items",
    type: "table",
    description: "Line items on each order.",
    columnCount: 6,
    columns: [
      { name: "id", dataType: "bigint", nullable: false, primaryKey: true },
      {
        name: "order_id",
        dataType: "bigint",
        nullable: false,
        foreignKey: { table: "orders", column: "id" },
      },
      {
        name: "product_id",
        dataType: "bigint",
        nullable: false,
        foreignKey: { table: "products", column: "id" },
      },
      {
        name: "quantity",
        dataType: "int",
        nullable: false,
        isNumeric: true,
        plannerRole: "metric",
      },
      {
        name: "unit_price",
        dataType: "numeric(12,2)",
        nullable: false,
        isNumeric: true,
        plannerRole: "metric",
      },
      {
        name: "line_total",
        dataType: "numeric(14,2)",
        nullable: false,
        isNumeric: true,
        plannerRole: "metric",
      },
    ],
  },
  {
    name: "products",
    type: "table",
    description: "Product catalog.",
    columnCount: 5,
    columns: [
      { name: "id", dataType: "bigint", nullable: false, primaryKey: true },
      { name: "name", dataType: "varchar(160)", nullable: false, plannerRole: "dimension" },
      {
        name: "sku",
        dataType: "varchar(64)",
        nullable: false,
        unique: true,
        plannerRole: "identifier",
      },
      { name: "category", dataType: "varchar(80)", nullable: true, plannerRole: "dimension" },
      {
        name: "unit_price",
        dataType: "numeric(12,2)",
        nullable: false,
        isNumeric: true,
        plannerRole: "metric",
      },
    ],
  },
  {
    name: "payments",
    type: "table",
    description: "Payments applied to orders.",
    columnCount: 6,
    columns: [
      { name: "id", dataType: "bigint", nullable: false, primaryKey: true },
      {
        name: "order_id",
        dataType: "bigint",
        nullable: false,
        foreignKey: { table: "orders", column: "id" },
      },
      {
        name: "customer_id",
        dataType: "bigint",
        nullable: false,
        foreignKey: { table: "customers", column: "id" },
      },
      {
        name: "amount",
        dataType: "numeric(12,2)",
        nullable: false,
        isNumeric: true,
        plannerRole: "metric",
      },
      { name: "method", dataType: "varchar(24)", nullable: false, plannerRole: "dimension" },
      {
        name: "paid_at",
        dataType: "timestamp",
        nullable: false,
        isDate: true,
        plannerRole: "date",
      },
    ],
  },
  {
    name: "invoices",
    type: "table",
    description: "Invoices issued to customers.",
    columnCount: 5,
    columns: [
      { name: "id", dataType: "bigint", nullable: false, primaryKey: true },
      {
        name: "order_id",
        dataType: "bigint",
        nullable: false,
        foreignKey: { table: "orders", column: "id" },
      },
      {
        name: "gross_amount",
        dataType: "numeric(12,2)",
        nullable: false,
        isNumeric: true,
        plannerRole: "metric",
      },
      {
        name: "tax_amount",
        dataType: "numeric(12,2)",
        nullable: false,
        isNumeric: true,
        plannerRole: "metric",
      },
      {
        name: "issued_at",
        dataType: "timestamp",
        nullable: false,
        isDate: true,
        plannerRole: "date",
      },
    ],
  },
];

export const MOCK_GRAPH: RelationshipGraph = {
  nodes: [
    { id: "customers", label: "customers" },
    { id: "orders", label: "orders" },
    { id: "order_items", label: "order_items" },
    { id: "products", label: "products" },
    { id: "payments", label: "payments" },
    { id: "invoices", label: "invoices" },
  ],
  edges: [
    {
      id: "e1",
      from: "orders",
      to: "customers",
      fromColumn: "customer_id",
      toColumn: "id",
      kind: "fk",
      cardinality: "one-to-many",
      confidence: 1,
      source: "information_schema",
      safeForPlanner: true,
    },
    {
      id: "e2",
      from: "order_items",
      to: "orders",
      fromColumn: "order_id",
      toColumn: "id",
      kind: "fk",
      cardinality: "one-to-many",
      confidence: 1,
      source: "information_schema",
      safeForPlanner: true,
    },
    {
      id: "e3",
      from: "order_items",
      to: "products",
      fromColumn: "product_id",
      toColumn: "id",
      kind: "fk",
      cardinality: "one-to-many",
      confidence: 1,
      source: "information_schema",
      safeForPlanner: true,
    },
    {
      id: "e4",
      from: "payments",
      to: "orders",
      fromColumn: "order_id",
      toColumn: "id",
      kind: "fk",
      cardinality: "one-to-many",
      confidence: 1,
      source: "information_schema",
      safeForPlanner: true,
    },
    {
      id: "e5",
      from: "payments",
      to: "customers",
      fromColumn: "customer_id",
      toColumn: "id",
      kind: "inferred",
      cardinality: "one-to-many",
      confidence: 0.82,
      source: "profiler_inferred",
      safeForPlanner: true,
    },
    {
      id: "e6",
      from: "invoices",
      to: "orders",
      fromColumn: "order_id",
      toColumn: "id",
      kind: "fk",
      cardinality: "one-to-one",
      confidence: 1,
      source: "information_schema",
      safeForPlanner: true,
    },
    {
      id: "e7",
      from: "invoices",
      to: "customers",
      fromColumn: "order_id",
      toColumn: "id",
      kind: "unsafe",
      cardinality: "many-to-many",
      confidence: 0.31,
      source: "text_similarity",
      safeForPlanner: false,
    },
  ],
};

export const MOCK_GLOSSARY: GlossaryEntry[] = [
  {
    id: "g1",
    term: "revenue",
    description: "Total money received from customers for goods or services.",
    candidateTables: ["payments", "invoices", "orders"],
    candidateColumns: ["payments.amount", "invoices.gross_amount", "orders.total_amount"],
    synonyms: ["sales", "turnover", "receipts"],
    source: "auto",
    confidence: 0.71,
    updatedAt: Date.now() - 1000 * 60 * 60 * 24 * 3,
  },
  {
    id: "g2",
    term: "active customer",
    description: "A customer whose status is 'active'.",
    candidateTables: ["customers"],
    candidateColumns: ["customers.status"],
    synonyms: ["current customer"],
    source: "auto",
    confidence: 0.92,
    updatedAt: Date.now() - 1000 * 60 * 60 * 24 * 5,
  },
  {
    id: "g3",
    term: "last month",
    description: "Previous calendar month relative to today.",
    candidateTables: [],
    candidateColumns: [],
    synonyms: ["previous month"],
    source: "builtin",
    confidence: 1,
    updatedAt: Date.now() - 1000 * 60 * 60 * 24 * 14,
  },
];

export const MOCK_KB: KnowledgeBaseStatus = {
  database: "shop_ops",
  schemaHash: "sha256:8f4d…c19a",
  lastBuildAt: Date.now() - 1000 * 60 * 42,
  tableCount: MOCK_TABLES.length,
  columnCount: MOCK_TABLES.reduce((n, t) => n + t.columnCount, 0),
  relationshipCount: MOCK_GRAPH.edges.length,
  glossaryTermCount: MOCK_GLOSSARY.length,
  vectorIndex: "ready",
  semanticEnrichment: "local_ollama",
  stale: false,
  stages: [
    { name: "Reading schema", status: "done" },
    { name: "Profiling data", status: "done" },
    { name: "Extracting schema facts", status: "done" },
    { name: "Building semantic metadata", status: "done" },
    { name: "Building business glossary", status: "done" },
    { name: "Building Relationship Graph", status: "done" },
    { name: "Creating vector index", status: "done" },
    { name: "Ready", status: "done" },
  ],
  buildLog: [
    { at: Date.now() - 1000 * 60 * 42, message: "Knowledge base build complete.", level: "info" },
    {
      at: Date.now() - 1000 * 60 * 43,
      message: "Vector index built (768 embeddings).",
      level: "info",
    },
    { at: Date.now() - 1000 * 60 * 45, message: "6 tables profiled, 37 columns.", level: "info" },
    {
      at: Date.now() - 1000 * 60 * 46,
      message: "1 low-confidence relationship marked unsafe.",
      level: "warn",
    },
  ],
};

function normalize(q: string) {
  return q.toLowerCase().replace(/\s+/g, " ").trim();
}

export function mockQueryFor(question: string): QueryResponse {
  const q = normalize(question);
  const now = performance.now();
  const base = {
    question,
    reason: "",
    stages: [
      { name: "Parsed", status: "done" as const },
      { name: "Evidence retrieved", status: "done" as const },
      { name: "Intent resolved", status: "done" as const },
      { name: "Join path resolved", status: "done" as const },
      { name: "Plan built", status: "done" as const },
      { name: "SQL generated", status: "done" as const },
      { name: "Validated", status: "done" as const },
      { name: "Executed", status: "done" as const },
    ],
    validation: {
      valid: true,
      message: "SELECT-only query passed all validator checks.",
      checks: [
        { name: "SELECT-only", passed: true },
        { name: "No DDL / DML", passed: true },
        { name: "Authorized join path", passed: true },
        { name: "Aggregate grain safe", passed: true },
      ],
    },
  };

  if (/(delete|drop|update|insert|truncate|alter)/i.test(q)) {
    return {
      route: "blocked_unsafe",
      reason:
        "The request contained a write or DDL keyword. SQLSense only executes validated read-only SELECT queries.",
      question,
      stages: [
        { name: "Parsed", status: "done" },
        { name: "Blocked by safety policy", status: "failed" },
      ],
      validation: { valid: false, message: "Write / DDL keywords are blocked before planning." },
    };
  }

  if (/(revenue|sales|amount)\b/.test(q) && !/(payment|invoice|order)/.test(q)) {
    return {
      route: "cannot_plan_safely",
      reason:
        "More than one candidate amount column matched. Choose which value SQLSense should use.",
      question,
      queryShape: "aggregate",
      ambiguityChoices: [
        {
          id: "orders.total_amount",
          label: "orders.total_amount",
          description: "Order-level gross before payments.",
        },
        {
          id: "payments.amount",
          label: "payments.amount",
          description: "Amount actually received from the customer.",
        },
        {
          id: "invoices.gross_amount",
          label: "invoices.gross_amount",
          description: "Invoiced amount including tax.",
        },
      ],
      evidence: {
        schemaMatches: [
          { table: "orders", column: "total_amount", score: 0.71, source: "schema_facts" },
          { table: "payments", column: "amount", score: 0.68, source: "schema_facts" },
          { table: "invoices", column: "gross_amount", score: 0.64, source: "schema_facts" },
        ],
        glossaryMatches: [{ term: "revenue", score: 0.82, source: "glossary" }],
      },
      stages: [
        { name: "Parsed", status: "done" },
        { name: "Evidence retrieved", status: "done" },
        { name: "Intent resolved", status: "failed", detail: "ambiguous metric" },
      ],
      validation: { valid: false, message: "Cannot plan safely without clarification." },
    };
  }

  if (/product.*supplier|supplier.*product|warehouse.*customer/.test(q)) {
    return {
      route: "cannot_plan_safely",
      reason:
        "No authorized Relationship Graph path connects these tables within the supported depth. The candidate path was informational only.",
      question,
      queryShape: "multi_hop",
      stages: [
        { name: "Parsed", status: "done" },
        { name: "Evidence retrieved", status: "done" },
        { name: "Join path resolved", status: "failed", detail: "unauthorized path" },
      ],
      validation: { valid: false, message: "No safe join path within depth limit." },
    };
  }

  if (/(payments? .*(customer|city)|payment .*details|payments and customer)/.test(q)) {
    return {
      ...base,
      route: "deterministic_sql_required",
      reason: "Two-edge safe join across payments → orders → customers.",
      queryShape: "joined_lookup",
      sql: "SELECT p.id AS payment_id,\n       p.amount,\n       p.method,\n       p.paid_at,\n       c.name    AS customer_name,\n       c.city    AS customer_city\n  FROM payments p\n  JOIN orders    o ON o.id = p.order_id\n  JOIN customers c ON c.id = o.customer_id\n ORDER BY p.paid_at DESC\n LIMIT 25;",
      columns: [
        { key: "payment_id", label: "Payment", dataType: "bigint", numeric: true },
        { key: "amount", label: "Amount", dataType: "numeric", numeric: true },
        { key: "method", label: "Method", dataType: "text" },
        { key: "paid_at", label: "Paid at", dataType: "timestamp" },
        { key: "customer_name", label: "Customer", dataType: "text" },
        { key: "customer_city", label: "City", dataType: "text" },
      ],
      rows: Array.from({ length: 12 }).map((_, i) => ({
        payment_id: 10230 + i,
        amount: (Math.round((240 + i * 37.5) * 100) / 100).toFixed(2),
        method: ["card", "bank_transfer", "upi", "cash"][i % 4],
        paid_at: new Date(Date.now() - i * 3600_000 * 6)
          .toISOString()
          .slice(0, 19)
          .replace("T", " "),
        customer_name: ["Ada Lovelace", "Ravi Kumar", "Mei Chen", "Lucia Rossi", "Ken Adams"][
          i % 5
        ],
        customer_city: ["London", "Pune", "Shanghai", "Milan", "Toronto"][i % 5],
      })),
      rowCount: 12,
      executionTimeMs: Math.round(performance.now() - now) + 42,
      selectedJoinPath: [
        {
          fromTable: "payments",
          fromColumn: "order_id",
          toTable: "orders",
          toColumn: "id",
          relationshipType: "fk",
        },
        {
          fromTable: "orders",
          fromColumn: "customer_id",
          toTable: "customers",
          toColumn: "id",
          relationshipType: "fk",
        },
      ],
      planner: {
        baseTable: "payments",
        columns: ["p.id", "p.amount", "p.method", "p.paid_at", "c.name", "c.city"],
        metrics: ["payments.amount"],
        dimensions: ["customers.name", "customers.city", "payments.method"],
        sort: [{ column: "payments.paid_at", direction: "desc" }],
        limit: 25,
        confidence: 0.92,
        detectedShape: "joined_lookup",
      },
      evidence: {
        schemaMatches: [
          { table: "payments", column: "amount", score: 0.94, source: "schema_facts" },
          { table: "customers", score: 0.88, source: "vector" },
        ],
        glossaryMatches: [{ term: "customer", score: 0.9, source: "glossary" }],
      },
    };
  }

  if (/city|from pune|active customer/.test(q)) {
    return {
      ...base,
      route: "deterministic_sql_required",
      reason: "Single-table lookup with filter.",
      queryShape: "single_table",
      sql: "SELECT id, name, email, city, status, created_at\n  FROM customers\n WHERE status = 'active'\n   AND city = 'Pune'\n ORDER BY created_at DESC\n LIMIT 25;",
      columns: [
        { key: "id", label: "ID", dataType: "bigint", numeric: true },
        { key: "name", label: "Name" },
        { key: "email", label: "Email" },
        { key: "city", label: "City" },
        { key: "status", label: "Status" },
        { key: "created_at", label: "Created" },
      ],
      rows: Array.from({ length: 6 }).map((_, i) => ({
        id: 401 + i,
        name: ["Ravi Kumar", "Anita Rao", "Vikram Shah", "Priya Iyer", "Neha Joshi", "Arjun Mehta"][
          i
        ],
        email: `user${401 + i}@example.com`,
        city: "Pune",
        status: "active",
        created_at: new Date(Date.now() - i * 86400_000 * 3).toISOString().slice(0, 10),
      })),
      rowCount: 6,
      executionTimeMs: 18,
      selectedJoinPath: [],
      planner: {
        baseTable: "customers",
        columns: ["id", "name", "email", "city", "status", "created_at"],
        filters: [
          { column: "customers.status", op: "=", value: "'active'" },
          { column: "customers.city", op: "=", value: "'Pune'" },
        ],
        limit: 25,
        confidence: 0.97,
        detectedShape: "single_table",
      },
    };
  }

  if (/top .*customer|by total order|by customer city/.test(q)) {
    return {
      ...base,
      route: "deterministic_sql_required",
      reason: "Grouped aggregate across orders joined to customers.",
      queryShape: "joined_aggregate",
      sql: "SELECT c.name       AS customer,\n       c.city       AS city,\n       SUM(o.total_amount) AS total_orders\n  FROM orders o\n  JOIN customers c ON c.id = o.customer_id\n GROUP BY c.name, c.city\n ORDER BY total_orders DESC\n LIMIT 5;",
      columns: [
        { key: "customer", label: "Customer" },
        { key: "city", label: "City" },
        { key: "total_orders", label: "Total orders", numeric: true, dataType: "numeric" },
      ],
      rows: [
        { customer: "Ada Lovelace", city: "London", total_orders: "18420.00" },
        { customer: "Mei Chen", city: "Shanghai", total_orders: "14210.50" },
        { customer: "Lucia Rossi", city: "Milan", total_orders: "12980.75" },
        { customer: "Ken Adams", city: "Toronto", total_orders: "9840.10" },
        { customer: "Ravi Kumar", city: "Pune", total_orders: "8720.00" },
      ],
      rowCount: 5,
      executionTimeMs: 61,
      selectedJoinPath: [
        {
          fromTable: "orders",
          fromColumn: "customer_id",
          toTable: "customers",
          toColumn: "id",
          relationshipType: "fk",
        },
      ],
      planner: {
        baseTable: "orders",
        metrics: ["SUM(orders.total_amount) AS total_orders"],
        dimensions: ["customers.name", "customers.city"],
        sort: [{ column: "total_orders", direction: "desc" }],
        limit: 5,
        confidence: 0.95,
        detectedShape: "joined_aggregate",
      },
    };
  }

  if (/no results|empty/.test(q)) {
    return {
      ...base,
      route: "deterministic_sql_required",
      reason: "Query executed successfully but returned zero rows.",
      queryShape: "single_table",
      sql: "SELECT id, name FROM customers WHERE city = 'Atlantis' LIMIT 25;",
      columns: [
        { key: "id", label: "ID", numeric: true },
        { key: "name", label: "Name" },
      ],
      rows: [],
      rowCount: 0,
      executionTimeMs: 8,
      planner: { baseTable: "customers", detectedShape: "single_table", confidence: 0.96 },
    };
  }

  // default: generic lookup
  return {
    ...base,
    route: "deterministic_sql_required",
    reason: "Generic lookup against orders.",
    queryShape: "single_table",
    sql: "SELECT id, customer_id, status, total_amount, placed_at\n  FROM orders\n ORDER BY placed_at DESC\n LIMIT 25;",
    columns: [
      { key: "id", label: "Order", numeric: true },
      { key: "customer_id", label: "Customer ID", numeric: true },
      { key: "status", label: "Status" },
      { key: "total_amount", label: "Amount", numeric: true, dataType: "numeric" },
      { key: "placed_at", label: "Placed at" },
    ],
    rows: Array.from({ length: 8 }).map((_, i) => ({
      id: 5001 + i,
      customer_id: 400 + (i % 5),
      status: ["pending", "shipped", "paid", "cancelled"][i % 4],
      total_amount: (Math.round((120 + i * 42.75) * 100) / 100).toFixed(2),
      placed_at: new Date(Date.now() - i * 86400_000).toISOString().slice(0, 10),
    })),
    rowCount: 8,
    executionTimeMs: 21,
    planner: { baseTable: "orders", detectedShape: "single_table", confidence: 0.88 },
  };
}
