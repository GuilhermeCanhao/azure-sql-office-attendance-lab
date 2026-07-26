/*
    Verification for 003_create_core_reference_tables.sql.
    This script changes no data.
*/

SET NOCOUNT ON;

DECLARE @ExpectedTables table
(
    TableName sysname NOT NULL PRIMARY KEY,
    ExpectedColumnCount int NOT NULL,
    ExpectedNullableColumnCount int NOT NULL
);

INSERT INTO @ExpectedTables
(
    TableName,
    ExpectedColumnCount,
    ExpectedNullableColumnCount
)
VALUES
    (N'Office', 7, 0),
    (N'Department', 5, 0),
    (N'Person', 8, 1),
    (N'Device', 4, 0),
    (N'PersonDeviceAssignment', 6, 1),
    (N'AccessPoint', 7, 0);

IF EXISTS
(
    SELECT 1
    FROM @ExpectedTables AS expected
    LEFT JOIN sys.tables AS tables
        ON tables.name = expected.TableName
       AND tables.schema_id = SCHEMA_ID(N'core')
    WHERE tables.object_id IS NULL
)
    THROW 51010, 'One or more expected core reference tables are missing.', 1;

IF EXISTS
(
    SELECT 1
    FROM @ExpectedTables AS expected
    INNER JOIN sys.tables AS tables
        ON tables.name = expected.TableName
       AND tables.schema_id = SCHEMA_ID(N'core')
    CROSS APPLY
    (
        SELECT
            COUNT(*) AS ActualColumnCount,
            SUM(CASE WHEN columns.is_nullable = 1 THEN 1 ELSE 0 END)
                AS ActualNullableColumnCount
        FROM sys.columns AS columns
        WHERE columns.object_id = tables.object_id
    ) AS actual
    WHERE actual.ActualColumnCount <> expected.ExpectedColumnCount
       OR actual.ActualNullableColumnCount <> expected.ExpectedNullableColumnCount
)
    THROW 51011, 'A core reference table has unexpected column metadata.', 1;

IF EXISTS
(
    SELECT 1
    FROM sys.foreign_keys
    WHERE parent_object_id IN
    (
        OBJECT_ID(N'core.Person'),
        OBJECT_ID(N'core.PersonDeviceAssignment'),
        OBJECT_ID(N'core.AccessPoint')
    )
      AND (is_disabled = 1 OR is_not_trusted = 1)
)
    THROW 51012, 'A core reference foreign key is disabled or untrusted.', 1;

IF EXISTS
(
    SELECT 1
    FROM sys.check_constraints
    WHERE parent_object_id IN
    (
        OBJECT_ID(N'core.Office'),
        OBJECT_ID(N'core.Department'),
        OBJECT_ID(N'core.Person'),
        OBJECT_ID(N'core.Device'),
        OBJECT_ID(N'core.PersonDeviceAssignment'),
        OBJECT_ID(N'core.AccessPoint')
    )
      AND (is_disabled = 1 OR is_not_trusted = 1)
)
    THROW 51013, 'A core reference check constraint is disabled or untrusted.', 1;

IF NOT EXISTS
(
    SELECT 1
    FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'core.PersonDeviceAssignment')
      AND name = N'UX_core_PersonDeviceAssignment_Device_ValidFrom'
      AND is_unique = 1
      AND is_disabled = 0
)
    THROW 51014, 'The assignment validation index is missing or unusable.', 1;

DECLARE @AssignmentIndexId int =
(
    SELECT index_id
    FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'core.PersonDeviceAssignment')
      AND name = N'UX_core_PersonDeviceAssignment_Device_ValidFrom'
);

IF NOT EXISTS
(
    SELECT 1
    FROM sys.index_columns AS index_columns
    INNER JOIN sys.columns AS columns
        ON columns.object_id = index_columns.object_id
       AND columns.column_id = index_columns.column_id
    WHERE index_columns.object_id = OBJECT_ID(N'core.PersonDeviceAssignment')
      AND index_columns.index_id = @AssignmentIndexId
      AND index_columns.key_ordinal = 1
      AND columns.name = N'DeviceId'
)
OR NOT EXISTS
(
    SELECT 1
    FROM sys.index_columns AS index_columns
    INNER JOIN sys.columns AS columns
        ON columns.object_id = index_columns.object_id
       AND columns.column_id = index_columns.column_id
    WHERE index_columns.object_id = OBJECT_ID(N'core.PersonDeviceAssignment')
      AND index_columns.index_id = @AssignmentIndexId
      AND index_columns.key_ordinal = 2
      AND columns.name = N'ValidFrom'
)
OR
(
    SELECT COUNT(*)
    FROM sys.index_columns AS index_columns
    INNER JOIN sys.columns AS columns
        ON columns.object_id = index_columns.object_id
       AND columns.column_id = index_columns.column_id
    WHERE index_columns.object_id = OBJECT_ID(N'core.PersonDeviceAssignment')
      AND index_columns.index_id = @AssignmentIndexId
      AND index_columns.is_included_column = 1
      AND columns.name IN (N'ValidTo', N'PersonId')
) <> 2
    THROW 51015, 'The assignment validation index has unexpected columns.', 1;

SELECT
    CAST(SCHEMA_NAME(tables.schema_id) AS varchar(10)) AS SchemaName,
    CAST(tables.name AS varchar(30)) AS TableName,
    COUNT(columns.column_id) AS ColumnCount,
    SUM(CASE WHEN columns.is_nullable = 1 THEN 1 ELSE 0 END) AS NullableColumnCount
FROM sys.tables AS tables
INNER JOIN sys.columns AS columns
    ON columns.object_id = tables.object_id
WHERE tables.schema_id = SCHEMA_ID(N'core')
  AND tables.name IN
  (
      N'Office',
      N'Department',
      N'Person',
      N'Device',
      N'PersonDeviceAssignment',
      N'AccessPoint'
  )
GROUP BY tables.schema_id, tables.name
ORDER BY tables.name;

SELECT
    COUNT(DISTINCT tables.object_id) AS CoreReferenceTableCount,
    SUM(CASE WHEN constraints.type = 'PK' THEN 1 ELSE 0 END) AS PrimaryKeyCount,
    SUM(CASE WHEN constraints.type = 'F' THEN 1 ELSE 0 END) AS ForeignKeyCount,
    SUM(CASE WHEN constraints.type = 'UQ' THEN 1 ELSE 0 END) AS UniqueConstraintCount,
    SUM(CASE WHEN constraints.type = 'C' THEN 1 ELSE 0 END) AS CheckConstraintCount
FROM
(
    SELECT parent_object_id, type
    FROM sys.objects
    WHERE type IN ('PK', 'F', 'UQ', 'C')
) AS constraints
RIGHT JOIN sys.tables AS tables
    ON tables.object_id = constraints.parent_object_id
WHERE tables.schema_id = SCHEMA_ID(N'core')
  AND tables.name IN
  (
      N'Office',
      N'Department',
      N'Person',
      N'Device',
      N'PersonDeviceAssignment',
      N'AccessPoint'
  );

SELECT
    CAST(indexes.name AS varchar(60)) AS IndexName,
    indexes.is_unique AS IsUnique,
    SUM(CASE WHEN index_columns.key_ordinal > 0 THEN 1 ELSE 0 END)
        AS KeyColumnCount,
    SUM(CASE WHEN index_columns.is_included_column = 1 THEN 1 ELSE 0 END)
        AS IncludedColumnCount
FROM sys.indexes AS indexes
INNER JOIN sys.index_columns AS index_columns
    ON index_columns.object_id = indexes.object_id
   AND index_columns.index_id = indexes.index_id
WHERE indexes.object_id = OBJECT_ID(N'core.PersonDeviceAssignment')
  AND indexes.name = N'UX_core_PersonDeviceAssignment_Device_ValidFrom'
GROUP BY indexes.name, indexes.is_unique;
