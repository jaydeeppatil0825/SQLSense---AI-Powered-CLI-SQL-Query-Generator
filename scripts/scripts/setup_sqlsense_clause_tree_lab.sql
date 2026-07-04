DROP DATABASE IF EXISTS sqlsense_clause_tree_lab;
CREATE DATABASE sqlsense_clause_tree_lab;
USE sqlsense_clause_tree_lab;

CREATE TABLE project_receipts (
    receipt_id INT PRIMARY KEY AUTO_INCREMENT,
    receipt_code VARCHAR(30) NOT NULL,
    client_name VARCHAR(100) NOT NULL,
    payment_state VARCHAR(30) NOT NULL,
    invoice_amount DECIMAL(12,2) NOT NULL,
    collected_amount DECIMAL(12,2) NOT NULL,
    service_date DATE NOT NULL,
    department VARCHAR(60) NOT NULL,
    city VARCHAR(60) NOT NULL
);

INSERT INTO project_receipts
(receipt_code, client_name, payment_state, invoice_amount, collected_amount, service_date, department, city)
VALUES
('PR-001', 'Apex Motors',    'settled', 18000.00, 18000.00, '2026-01-05', 'cloud',      'Pune'),
('PR-002', 'Nova Foods',     'open',     7000.00,     0.00, '2026-01-12', 'support',    'Mumbai'),
('PR-003', 'Apex Motors',    'partial', 12000.00,  5000.00, '2026-01-22', 'cloud',      'Pune'),
('PR-004', 'Orion Textiles', 'settled', 24000.00, 24000.00, '2026-02-03', 'automation', 'Surat'),
('PR-005', 'Bright Retail',  'settled',  9000.00,  9000.00, '2026-02-10', 'support',    'Pune'),
('PR-006', 'Nova Foods',     'open',     4500.00,     0.00, '2026-02-16', 'support',    'Mumbai'),
('PR-007', 'Apex Motors',    'settled', 30000.00, 30000.00, '2026-03-01', 'cloud',      'Pune'),
('PR-008', 'Orion Textiles', 'partial', 15000.00,  6000.00, '2026-03-08', 'automation', 'Surat'),
('PR-009', 'Zen Labs',       'settled', 11000.00, 11000.00, '2026-03-14', 'analytics',  'Bengaluru'),
('PR-010', 'Bright Retail',  'open',     6500.00,     0.00, '2026-03-21', 'support',    'Pune'),
('PR-011', 'Zen Labs',       'partial',  8000.00,  3000.00, '2026-04-02', 'analytics',  'Bengaluru'),
('PR-012', 'Orion Textiles', 'settled', 28000.00, 28000.00, '2026-04-12', 'automation', 'Surat'),
('PR-013', 'Nova Foods',     'settled', 10000.00, 10000.00, '2026-04-20', 'support',    'Mumbai'),
('PR-014', 'Bright Retail',  'partial', 13000.00,  4000.00, '2026-05-01', 'support',    'Pune'),
('PR-015', 'Apex Motors',    'open',     9500.00,     0.00, '2026-05-07', 'cloud',      'Pune'),
('PR-016', 'Zen Labs',       'settled', 21000.00, 21000.00, '2026-05-15', 'analytics',  'Bengaluru');