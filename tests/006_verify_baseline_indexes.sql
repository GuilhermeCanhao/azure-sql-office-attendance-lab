/*
    Verification for 006_create_baseline_indexes.sql.
    This script changes no data.
*/

SET NOCOUNT ON;

DECLARE @ObjectId int = OBJECT_ID(N'core.AttendanceSignal');
DECLARE @IndexName sysname = N'IX_core_AttendanceSignal_DailySummaryRefresh';
DECLARE @IndexId int;

IF @ObjectId IS NULL
    THROW 51300, 'The core.AttendanceSignal table is missing.', 1;

SELECT @IndexId = indexes.index_id
FROM sys.indexes AS indexes
WHERE indexes.object_id = @ObjectId
  AND indexes.name = @IndexName
  AND indexes.type = 2
  AND indexes.is_unique = 0
  AND indexes.is_disabled = 0;

IF @IndexId IS NULL
    THROW 51301, 'The daily-summary refresh index is missing or unusable.', 1;

DECLARE @ExpectedColumns table
(
    ColumnName sysname NOT NULL,
    KeyOrdinal tinyint NOT NULL,
    IsIncludedColumn bit NOT NULL,
    PRIMARY KEY (ColumnName)
);

INSERT INTO @ExpectedColumns
(
    ColumnName,
    KeyOrdinal,
    IsIncludedColumn
)
VALUES
    (N'AttendanceDateLocal', 1, 0),
    (N'OfficeId', 2, 0),
    (N'PersonId', 3, 0),
    (N'SignalType', 0, 1),
    (N'ObservedAtUtc', 0, 1);

IF EXISTS
(
    SELECT
        expected.ColumnName,
        expected.KeyOrdinal,
        expected.IsIncludedColumn
    FROM @ExpectedColumns AS expected

    EXCEPT

    SELECT
        columns.name,
        index_columns.key_ordinal,
        index_columns.is_included_column
    FROM sys.index_columns AS index_columns
    INNER JOIN sys.columns AS columns
        ON columns.object_id = index_columns.object_id
       AND columns.column_id = index_columns.column_id
    WHERE index_columns.object_id = @ObjectId
      AND index_columns.index_id = @IndexId
)
OR EXISTS
(
    SELECT
        columns.name,
        index_columns.key_ordinal,
        index_columns.is_included_column
    FROM sys.index_columns AS index_columns
    INNER JOIN sys.columns AS columns
        ON columns.object_id = index_columns.object_id
       AND columns.column_id = index_columns.column_id
    WHERE index_columns.object_id = @ObjectId
      AND index_columns.index_id = @IndexId

    EXCEPT

    SELECT
        expected.ColumnName,
        expected.KeyOrdinal,
        expected.IsIncludedColumn
    FROM @ExpectedColumns AS expected
)
    THROW 51302, 'The daily-summary refresh index has unexpected columns or order.', 1;

SELECT
    CAST(SCHEMA_NAME(tables.schema_id) AS varchar(10)) AS SchemaName,
    CAST(tables.name AS varchar(30)) AS TableName,
    CAST(indexes.name AS varchar(60)) AS IndexName,
    indexes.is_unique AS IsUnique,
    indexes.is_disabled AS IsDisabled,
    SUM(CASE WHEN index_columns.key_ordinal > 0 THEN 1 ELSE 0 END)
        AS KeyColumnCount,
    SUM(CASE WHEN index_columns.is_included_column = 1 THEN 1 ELSE 0 END)
        AS IncludedColumnCount,
    CAST('PASS' AS varchar(10)) AS ShapeVerification
FROM sys.indexes AS indexes
INNER JOIN sys.tables AS tables
    ON tables.object_id = indexes.object_id
INNER JOIN sys.index_columns AS index_columns
    ON index_columns.object_id = indexes.object_id
   AND index_columns.index_id = indexes.index_id
WHERE indexes.object_id = @ObjectId
  AND indexes.index_id = @IndexId
GROUP BY
    tables.schema_id,
    tables.name,
    indexes.name,
    indexes.is_unique,
    indexes.is_disabled;

SELECT
    CAST(columns.name AS varchar(30)) AS ColumnName,
    CASE
        WHEN index_columns.is_included_column = 1 THEN 'INCLUDE'
        ELSE 'KEY'
    END AS ColumnRole,
    index_columns.key_ordinal AS KeyOrdinal
FROM sys.index_columns AS index_columns
INNER JOIN sys.columns AS columns
    ON columns.object_id = index_columns.object_id
   AND columns.column_id = index_columns.column_id
WHERE index_columns.object_id = @ObjectId
  AND index_columns.index_id = @IndexId
ORDER BY
    index_columns.is_included_column,
    index_columns.key_ordinal,
    index_columns.index_column_id;
