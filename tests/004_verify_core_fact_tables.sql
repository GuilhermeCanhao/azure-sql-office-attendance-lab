/*
    Verification for 004_create_core_fact_tables.sql.
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
    (N'AttendanceSignal', 11, 1),
    (N'DailyAttendanceSummary', 9, 0);

IF EXISTS
(
    SELECT 1
    FROM @ExpectedTables AS expected
    LEFT JOIN sys.tables AS tables
        ON tables.name = expected.TableName
       AND tables.schema_id = SCHEMA_ID(N'core')
    WHERE tables.object_id IS NULL
)
    THROW 51020, 'One or more expected core fact tables are missing.', 1;

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
    THROW 51021, 'A core fact table has unexpected column metadata.', 1;

IF EXISTS
(
    SELECT 1
    FROM sys.foreign_keys
    WHERE parent_object_id IN
    (
        OBJECT_ID(N'core.AttendanceSignal'),
        OBJECT_ID(N'core.DailyAttendanceSummary')
    )
      AND (is_disabled = 1 OR is_not_trusted = 1)
)
    THROW 51022, 'A core fact foreign key is disabled or untrusted.', 1;

IF EXISTS
(
    SELECT 1
    FROM sys.check_constraints
    WHERE parent_object_id IN
    (
        OBJECT_ID(N'core.AttendanceSignal'),
        OBJECT_ID(N'core.DailyAttendanceSummary')
    )
      AND (is_disabled = 1 OR is_not_trusted = 1)
)
    THROW 51023, 'A core fact check constraint is disabled or untrusted.', 1;

DECLARE @ExpectedIndexes table
(
    SchemaName sysname NOT NULL,
    TableName sysname NOT NULL,
    IndexName sysname NOT NULL,
    ExpectedKeyColumnCount int NOT NULL,
    PRIMARY KEY (SchemaName, TableName, IndexName)
);

INSERT INTO @ExpectedIndexes
(
    SchemaName,
    TableName,
    IndexName,
    ExpectedKeyColumnCount
)
VALUES
    (
        N'stage',
        N'ImportBatch',
        N'UX_stage_ImportBatch_ImportBatchId_SourceType',
        2
    ),
    (
        N'core',
        N'AccessPoint',
        N'UX_core_AccessPoint_AccessPointId_OfficeId',
        2
    ),
    (
        N'core',
        N'AttendanceSignal',
        N'UQ_core_AttendanceSignal_SourceLineage',
        2
    );

IF EXISTS
(
    SELECT 1
    FROM @ExpectedIndexes AS expected
    LEFT JOIN sys.schemas AS schemas
        ON schemas.name = expected.SchemaName
    LEFT JOIN sys.tables AS tables
        ON tables.schema_id = schemas.schema_id
       AND tables.name = expected.TableName
    LEFT JOIN sys.indexes AS indexes
        ON indexes.object_id = tables.object_id
       AND indexes.name = expected.IndexName
       AND indexes.is_unique = 1
       AND indexes.is_disabled = 0
    OUTER APPLY
    (
        SELECT COUNT(*) AS ActualKeyColumnCount
        FROM sys.index_columns AS index_columns
        WHERE index_columns.object_id = indexes.object_id
          AND index_columns.index_id = indexes.index_id
          AND index_columns.key_ordinal > 0
    ) AS actual
    WHERE indexes.index_id IS NULL
       OR actual.ActualKeyColumnCount <> expected.ExpectedKeyColumnCount
)
    THROW 51024, 'A required fact-integrity index is missing or malformed.', 1;

SELECT
    CAST(SCHEMA_NAME(tables.schema_id) AS varchar(10)) AS SchemaName,
    CAST(tables.name AS varchar(30)) AS TableName,
    COUNT(columns.column_id) AS ColumnCount,
    SUM(CASE WHEN columns.is_nullable = 1 THEN 1 ELSE 0 END) AS NullableColumnCount
FROM sys.tables AS tables
INNER JOIN sys.columns AS columns
    ON columns.object_id = tables.object_id
WHERE tables.schema_id = SCHEMA_ID(N'core')
  AND tables.name IN (N'AttendanceSignal', N'DailyAttendanceSummary')
GROUP BY tables.schema_id, tables.name
ORDER BY tables.name;

SELECT
    COUNT(DISTINCT tables.object_id) AS CoreFactTableCount,
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
  AND tables.name IN (N'AttendanceSignal', N'DailyAttendanceSummary');

SELECT
    CAST(SCHEMA_NAME(tables.schema_id) AS varchar(10)) AS SchemaName,
    CAST(tables.name AS varchar(30)) AS TableName,
    CAST(indexes.name AS varchar(60)) AS IndexName,
    indexes.is_unique AS IsUnique,
    SUM(CASE WHEN index_columns.key_ordinal > 0 THEN 1 ELSE 0 END)
        AS KeyColumnCount
FROM sys.indexes AS indexes
INNER JOIN sys.tables AS tables
    ON tables.object_id = indexes.object_id
INNER JOIN sys.index_columns AS index_columns
    ON index_columns.object_id = indexes.object_id
   AND index_columns.index_id = indexes.index_id
WHERE indexes.name IN
(
    N'UX_stage_ImportBatch_ImportBatchId_SourceType',
    N'UX_core_AccessPoint_AccessPointId_OfficeId',
    N'UQ_core_AttendanceSignal_SourceLineage'
)
GROUP BY tables.schema_id, tables.name, indexes.name, indexes.is_unique
ORDER BY SchemaName, TableName, IndexName;
