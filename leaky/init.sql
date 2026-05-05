CREATE DATABASE IF NOT EXISTS company_db;
USE company_db;

CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(50) NOT NULL,
    password VARCHAR(50) NOT NULL
);

INSERT INTO users (username, password)
SELECT 'admin', 'admin123'
WHERE NOT EXISTS (
    SELECT 1 FROM users WHERE username = 'admin'
);

CREATE TABLE IF NOT EXISTS flags (
    id INT AUTO_INCREMENT PRIMARY KEY,
    flag VARCHAR(100) NOT NULL
);

INSERT INTO flags (flag)
SELECT 'BSOFT_FLAG{devops_leak_exploited}'
WHERE NOT EXISTS (
    SELECT 1 FROM flags WHERE flag = 'BSOFT_FLAG{devops_leak_exploited}'
);
