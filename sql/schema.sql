-- Control table, for reference.
--
-- This is the only table the pipeline creates explicitly, because it is the
-- one that has to be right: it holds the watermark that decides what gets
-- downloaded next. The pipeline applies this DDL itself on every run (see
-- src/control.py), so running this file by hand is optional.
--
-- The report tables (branch_sales, inventory_movements) are created on first
-- load by pandas' to_sql, which infers their types. See the README for why
-- that is a limitation rather than a design choice.

IF OBJECT_ID('dbo.etl_load_history', 'U') IS NULL
CREATE TABLE dbo.etl_load_history (
    subject NVARCHAR(255) NOT NULL,
    received_at DATETIME2 NOT NULL,
    attachment NVARCHAR(400) NOT NULL,
    destination_table NVARCHAR(128) NOT NULL,
    rows_loaded INT NOT NULL,
    loaded_at DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
);

-- How the pipeline resumes: the latest email already loaded, per subject.
-- Anything newer than this is what the next run fetches.
WITH ranked AS (
    SELECT
        subject,
        received_at,
        ROW_NUMBER() OVER (PARTITION BY subject ORDER BY received_at DESC) AS rn
    FROM dbo.etl_load_history
)
SELECT subject, received_at FROM ranked WHERE rn = 1;
