/*
    Verification for 002_create_stage_tables.sql.
    This script changes no data.
*/

SET NOCOUNT ON;

DECLARE @ExpectedTables table
(
    TableName sysname NOT NULL PRIMARY KEY,
    ExpectedColumnCount int NOT NULL
);

INSERT INTO @ExpectedTables (TableName, ExpectedColumnCount)
VALUES
    (N'ImportBatch', 11),
    (N'CardAccessEvent', 8),
    (N'WifiObservation', 9),
    (N'ImportError', 7);

IF EXISTS
(
    SELECT 1
    FROM @ExpectedTables AS expected
    LEFT JOIN sys.tables AS tables
        ON tables.name = expected.TableName
       AND tables.schema_id = SCHEMA_ID(N'stage')
    WHERE tables.object_id IS NULL
)
    THROW 51000, 'One or more expected stage tables are missing.', 1;

IF EXISTS
(
    SELECT 1
    FROM @ExpectedTables AS expected
    INNER JOIN sys.tables AS tables
        ON tables.name = expected.TableName
       AND tables.schema_id = SCHEMA_ID(N'stage')
    CROSS APPLY
    (
        SELECT COUNT(*) AS ActualColumnCount
        FROM sys.columns AS columns
        WHERE columns.object_id = tables.object_id
    ) AS actual
    WHERE actual.ActualColumnCount <> expected.ExpectedColumnCount
)
    THROW 51001, 'An expected stage table has an unexpected column count.', 1;

IF EXISTS
(
    SELECT 1
    FROM sys.foreign_keys
    WHERE parent_object_id IN
    (
        OBJECT_ID(N'stage.CardAccessEvent'),
        OBJECT_ID(N'stage.WifiObservation'),
        OBJECT_ID(N'stage.ImportError')
    )
      AND (is_disabled = 1 OR is_not_trusted = 1)
)
    THROW 51002, 'A stage foreign key is disabled or untrusted.', 1;

SELECT
    CAST(SCHEMA_NAME(tables.schema_id) AS varchar(10)) AS SchemaName,
    CAST(tables.name AS varchar(30)) AS TableName,
    COUNT(columns.column_id) AS ColumnCount,
    SUM(CASE WHEN columns.is_nullable = 1 THEN 1 ELSE 0 END) AS NullableColumnCount
FROM sys.tables AS tables
INNER JOIN sys.columns AS columns
    ON columns.object_id = tables.object_id
WHERE tables.schema_id = SCHEMA_ID(N'stage')
  AND tables.name IN
  (
      N'ImportBatch',
      N'CardAccessEvent',
      N'WifiObservation',
      N'ImportError'
  )
GROUP BY tables.schema_id, tables.name
ORDER BY tables.name;

SELECT
    COUNT(DISTINCT tables.object_id) AS StageTableCount,
    SUM(CASE WHEN constraints.type = 'PK' THEN 1 ELSE 0 END) AS PrimaryKeyCount,
    SUM(CASE WHEN constraints.type = 'F' THEN 1 ELSE 0 END) AS ForeignKeyCount,
    SUM(CASE WHEN constraints.type = 'UQ' THEN 1 ELSE 0 END) AS UniqueConstraintCount,
    SUM(CASE WHEN constraints.type = 'C' THEN 1 ELSE 0 END) AS CheckConstraintCount
FROM
(
    SELECT object_id, parent_object_id, type
    FROM sys.objects
    WHERE type IN ('PK', 'F', 'UQ', 'C')
) AS constraints
RIGHT JOIN sys.tables AS tables
    ON tables.object_id = constraints.parent_object_id
WHERE tables.schema_id = SCHEMA_ID(N'stage')
  AND tables.name IN
  (
      N'ImportBatch',
      N'CardAccessEvent',
      N'WifiObservation',
      N'ImportError'
  );
