-- SQLSense Current-Scope Business Lab
-- Database: sqlsense_current_scope_business_lab
-- Dialect: MySQL 8.x
-- Purpose:
--   Clean realistic test database for SQLSense current controlled scope:
--   single-table queries, filters, aggregates, grouping, ranking,
--   direct joins, and safe 2-edge / 3-table many-to-one joins.
--
-- Notes:
--   - No ambiguous duplicate business columns like payment_status on multiple tables.
--   - Supported multi-hop questions are linear paths with max 2 edges.

DROP DATABASE IF EXISTS sqlsense_current_scope_business_lab;
CREATE DATABASE sqlsense_current_scope_business_lab;
USE sqlsense_current_scope_business_lab;

CREATE TABLE customers (
    customer_id INT PRIMARY KEY,
    customer_name VARCHAR(100) NOT NULL,
    city VARCHAR(60) NOT NULL,
    region VARCHAR(40) NOT NULL,
    segment VARCHAR(40) NOT NULL,
    customer_status VARCHAR(20) NOT NULL,
    created_date DATE NOT NULL
);

CREATE TABLE suppliers (
    supplier_id INT PRIMARY KEY,
    supplier_name VARCHAR(100) NOT NULL,
    city VARCHAR(60) NOT NULL,
    region VARCHAR(40) NOT NULL,
    supplier_status VARCHAR(20) NOT NULL,
    rating DECIMAL(3,2) NOT NULL
);

CREATE TABLE categories (
    category_id INT PRIMARY KEY,
    category_name VARCHAR(80) NOT NULL,
    category_status VARCHAR(20) NOT NULL
);

CREATE TABLE products (
    product_id INT PRIMARY KEY,
    product_name VARCHAR(120) NOT NULL,
    category_id INT NOT NULL,
    supplier_id INT NOT NULL,
    unit_price DECIMAL(12,2) NOT NULL,
    stock_quantity INT NOT NULL,
    product_status VARCHAR(20) NOT NULL,
    FOREIGN KEY (category_id) REFERENCES categories(category_id),
    FOREIGN KEY (supplier_id) REFERENCES suppliers(supplier_id)
);

CREATE TABLE orders (
    order_id INT PRIMARY KEY,
    customer_id INT NOT NULL,
    order_date DATE NOT NULL,
    order_status VARCHAR(30) NOT NULL,
    total_amount DECIMAL(12,2) NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);

CREATE TABLE order_items (
    order_item_id INT PRIMARY KEY,
    order_id INT NOT NULL,
    product_id INT NOT NULL,
    quantity INT NOT NULL,
    unit_price DECIMAL(12,2) NOT NULL,
    line_amount DECIMAL(12,2) NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(order_id),
    FOREIGN KEY (product_id) REFERENCES products(product_id)
);

CREATE TABLE payments (
    payment_id INT PRIMARY KEY,
    order_id INT NOT NULL,
    payment_date DATE NOT NULL,
    payment_method VARCHAR(40) NOT NULL,
    payment_status VARCHAR(30) NOT NULL,
    payment_amount DECIMAL(12,2) NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(order_id)
);

CREATE TABLE shipments (
    shipment_id INT PRIMARY KEY,
    order_id INT NOT NULL,
    shipped_date DATE NOT NULL,
    delivery_city VARCHAR(60) NOT NULL,
    shipment_status VARCHAR(30) NOT NULL,
    shipping_cost DECIMAL(10,2) NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(order_id)
);

INSERT INTO customers VALUES
(1, 'Aarav Industries', 'Pune', 'West', 'Enterprise', 'Active', '2025-01-12'),
(2, 'Meera Retail Hub', 'Mumbai', 'West', 'Retail', 'Active', '2025-02-18'),
(3, 'Kavya Auto Works', 'Chakan', 'West', 'Automotive', 'Active', '2025-03-07'),
(4, 'Rohan Engineering', 'Nashik', 'West', 'Manufacturing', 'Inactive', '2024-11-20'),
(5, 'Anaya Foods', 'Bengaluru', 'South', 'Retail', 'Active', '2025-05-11'),
(6, 'Vivaan Traders', 'Delhi', 'North', 'Wholesale', 'Active', '2025-06-03'),
(7, 'Isha Components', 'Pune', 'West', 'Automotive', 'Active', '2025-06-29'),
(8, 'Dev Logistics', 'Hyderabad', 'South', 'Logistics', 'Inactive', '2024-09-14'),
(9, 'Nisha Pharma', 'Ahmedabad', 'West', 'Healthcare', 'Active', '2025-08-09'),
(10, 'Omkar Machines', 'Coimbatore', 'South', 'Manufacturing', 'Active', '2025-09-01'),
(11, 'Reyansh Stores', 'Jaipur', 'North', 'Retail', 'Active', '2025-10-10'),
(12, 'Saanvi Metals', 'Surat', 'West', 'Manufacturing', 'Active', '2025-12-05');

INSERT INTO suppliers VALUES
(1, 'Precision Fasteners India', 'Pune', 'West', 'Active', 4.70),
(2, 'Metro Electrical Supply', 'Mumbai', 'West', 'Active', 4.25),
(3, 'South Tools Corporation', 'Bengaluru', 'South', 'Active', 4.55),
(4, 'North Packaging Traders', 'Delhi', 'North', 'Inactive', 3.80),
(5, 'Chakan Auto Components', 'Chakan', 'West', 'Active', 4.90),
(6, 'Surat Metal Works', 'Surat', 'West', 'Active', 4.10),
(7, 'Hyderabad Safety Gear', 'Hyderabad', 'South', 'Active', 4.35),
(8, 'Jaipur Office Systems', 'Jaipur', 'North', 'Inactive', 3.60);

INSERT INTO categories VALUES
(1, 'Fasteners', 'Active'),
(2, 'Electrical', 'Active'),
(3, 'Cutting Tools', 'Active'),
(4, 'Packaging', 'Active'),
(5, 'Auto Components', 'Active'),
(6, 'Safety Equipment', 'Active'),
(7, 'Office Supplies', 'Inactive');

INSERT INTO products VALUES
(1, 'M8 T Slot Nut', 1, 1, 42.00, 450, 'Active'),
(2, 'M10 T Slot Nut', 1, 1, 58.00, 380, 'Active'),
(3, 'Cable Harness Kit', 2, 2, 1250.00, 35, 'Active'),
(4, 'Industrial Relay', 2, 2, 780.00, 70, 'Active'),
(5, 'Carbide End Mill 8mm', 3, 3, 1850.00, 28, 'Active'),
(6, 'Carbide Drill 6mm', 3, 3, 940.00, 44, 'Active'),
(7, 'Corrugated Box Medium', 4, 4, 32.00, 800, 'Inactive'),
(8, 'Bubble Wrap Roll', 4, 4, 410.00, 120, 'Active'),
(9, 'Brake Mounting Bracket', 5, 5, 2650.00, 52, 'Active'),
(10, 'CNC Fixture Clamp', 5, 5, 3200.00, 18, 'Active'),
(11, 'Safety Helmet', 6, 7, 360.00, 95, 'Active'),
(12, 'Cut Resistant Gloves', 6, 7, 145.00, 240, 'Active'),
(13, 'Printer Paper Box', 7, 8, 980.00, 25, 'Inactive'),
(14, 'Steel Spacer Bush', 5, 6, 165.00, 610, 'Active'),
(15, 'Allen Bolt Pack', 1, 6, 210.00, 300, 'Active');

INSERT INTO orders VALUES
(1001, 1, '2026-01-05', 'Delivered', 72500.00),
(1002, 2, '2026-01-18', 'Delivered', 18500.00),
(1003, 3, '2026-02-02', 'Processing', 96000.00),
(1004, 4, '2026-02-14', 'Cancelled', 12500.00),
(1005, 5, '2026-03-01', 'Delivered', 43200.00),
(1006, 6, '2026-03-16', 'Shipped', 68500.00),
(1007, 7, '2026-04-03', 'Delivered', 118000.00),
(1008, 8, '2026-04-19', 'Processing', 22000.00),
(1009, 9, '2026-05-08', 'Delivered', 54000.00),
(1010, 10, '2026-05-22', 'Shipped', 77500.00),
(1011, 11, '2026-06-04', 'Delivered', 30500.00),
(1012, 12, '2026-06-20', 'Processing', 88750.00),
(1013, 1, '2026-07-02', 'Delivered', 42500.00),
(1014, 3, '2026-07-18', 'Delivered', 133500.00),
(1015, 7, '2026-08-09', 'Shipped', 64250.00);

INSERT INTO order_items VALUES
(1, 1001, 9, 10, 2650.00, 26500.00),
(2, 1001, 10, 8, 3200.00, 25600.00),
(3, 1001, 5, 11, 1850.00, 20350.00),
(4, 1002, 1, 200, 42.00, 8400.00),
(5, 1002, 2, 100, 58.00, 5800.00),
(6, 1002, 12, 30, 145.00, 4350.00),
(7, 1003, 10, 20, 3200.00, 64000.00),
(8, 1003, 9, 12, 2650.00, 31800.00),
(9, 1004, 8, 20, 410.00, 8200.00),
(10, 1004, 11, 12, 360.00, 4320.00),
(11, 1005, 3, 20, 1250.00, 25000.00),
(12, 1005, 4, 15, 780.00, 11700.00),
(13, 1005, 11, 18, 360.00, 6480.00),
(14, 1006, 5, 18, 1850.00, 33300.00),
(15, 1006, 6, 20, 940.00, 18800.00),
(16, 1006, 15, 78, 210.00, 16380.00),
(17, 1007, 9, 25, 2650.00, 66250.00),
(18, 1007, 10, 16, 3200.00, 51200.00),
(19, 1007, 14, 3, 165.00, 495.00),
(20, 1008, 13, 10, 980.00, 9800.00),
(21, 1008, 8, 20, 410.00, 8200.00),
(22, 1008, 1, 95, 42.00, 3990.00),
(23, 1009, 3, 28, 1250.00, 35000.00),
(24, 1009, 4, 15, 780.00, 11700.00),
(25, 1009, 12, 50, 145.00, 7250.00),
(26, 1010, 5, 25, 1850.00, 46250.00),
(27, 1010, 6, 20, 940.00, 18800.00),
(28, 1010, 11, 35, 360.00, 12600.00),
(29, 1011, 1, 250, 42.00, 10500.00),
(30, 1011, 2, 180, 58.00, 10440.00),
(31, 1011, 15, 45, 210.00, 9450.00),
(32, 1012, 9, 18, 2650.00, 47700.00),
(33, 1012, 10, 12, 3200.00, 38400.00),
(34, 1012, 14, 16, 165.00, 2640.00),
(35, 1013, 3, 18, 1250.00, 22500.00),
(36, 1013, 4, 10, 780.00, 7800.00),
(37, 1013, 12, 84, 145.00, 12180.00),
(38, 1014, 10, 30, 3200.00, 96000.00),
(39, 1014, 9, 14, 2650.00, 37100.00),
(40, 1014, 14, 2, 165.00, 330.00),
(41, 1015, 5, 22, 1850.00, 40700.00),
(42, 1015, 6, 18, 940.00, 16920.00),
(43, 1015, 11, 18, 360.00, 6480.00);

INSERT INTO payments VALUES
(5001, 1001, '2026-01-06', 'Bank Transfer', 'Completed', 72500.00),
(5002, 1002, '2026-01-19', 'UPI', 'Completed', 18500.00),
(5003, 1003, '2026-02-05', 'Bank Transfer', 'Pending', 50000.00),
(5004, 1004, '2026-02-15', 'Card', 'Failed', 0.00),
(5005, 1005, '2026-03-03', 'UPI', 'Completed', 43200.00),
(5006, 1006, '2026-03-18', 'Bank Transfer', 'Completed', 68500.00),
(5007, 1007, '2026-04-05', 'Bank Transfer', 'Completed', 118000.00),
(5008, 1008, '2026-04-22', 'Card', 'Pending', 10000.00),
(5009, 1009, '2026-05-09', 'UPI', 'Completed', 54000.00),
(5010, 1010, '2026-05-24', 'Bank Transfer', 'Completed', 77500.00),
(5011, 1011, '2026-06-05', 'UPI', 'Completed', 30500.00),
(5012, 1012, '2026-06-23', 'Bank Transfer', 'Pending', 45000.00),
(5013, 1013, '2026-07-04', 'Card', 'Completed', 42500.00),
(5014, 1014, '2026-07-20', 'Bank Transfer', 'Completed', 133500.00),
(5015, 1015, '2026-08-11', 'UPI', 'Completed', 64250.00);

INSERT INTO shipments VALUES
(7001, 1001, '2026-01-07', 'Pune', 'Delivered', 1400.00),
(7002, 1002, '2026-01-21', 'Mumbai', 'Delivered', 750.00),
(7003, 1003, '2026-02-08', 'Chakan', 'In Transit', 2200.00),
(7004, 1004, '2026-02-16', 'Nashik', 'Cancelled', 0.00),
(7005, 1005, '2026-03-05', 'Bengaluru', 'Delivered', 1600.00),
(7006, 1006, '2026-03-20', 'Delhi', 'In Transit', 2100.00),
(7007, 1007, '2026-04-06', 'Pune', 'Delivered', 2600.00),
(7008, 1008, '2026-04-24', 'Hyderabad', 'Packed', 900.00),
(7009, 1009, '2026-05-10', 'Ahmedabad', 'Delivered', 1300.00),
(7010, 1010, '2026-05-25', 'Coimbatore', 'In Transit', 2400.00),
(7011, 1011, '2026-06-06', 'Jaipur', 'Delivered', 950.00),
(7012, 1012, '2026-06-25', 'Surat', 'Packed', 1800.00),
(7013, 1013, '2026-07-05', 'Pune', 'Delivered', 1150.00),
(7014, 1014, '2026-07-21', 'Chakan', 'Delivered', 2700.00),
(7015, 1015, '2026-08-12', 'Pune', 'In Transit', 1550.00);
