-- SOLO SQL Server / Azure SQL. Confirmar motor antes de ejecutar.
-- Consulta de metadatos, sin leer registros de clientes ni cambiar objetos.
DECLARE @Esquema sysname = N'REEMPLAZAR_ESQUEMA';
DECLARE @Vista sysname = N'vw_chatbot_mom'; -- Confirmar nombre completo real.

SELECT
    s.name AS Esquema,
    v.name AS Vista,
    c.column_id AS OrdenColumna,
    c.name AS Columna,
    t.name AS TipoSQL,
    c.max_length AS LongitudBytes,
    c.precision AS PrecisionNumerica,
    c.scale AS Escala,
    c.is_nullable AS PermiteNulos
FROM sys.views AS v
INNER JOIN sys.schemas AS s ON s.schema_id = v.schema_id
INNER JOIN sys.columns AS c ON c.object_id = v.object_id
INNER JOIN sys.types AS t ON t.user_type_id = c.user_type_id
WHERE s.name = @Esquema AND v.name = @Vista
ORDER BY c.column_id;
