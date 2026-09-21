"""
Prueba de concepto: agente Text-to-SQL sobre Azure SQL DB usando Azure OpenAI.

Flujo:
  1. Recibe una pregunta en lenguaje natural.
  2. Arma un prompt con el esquema de la base y unos pocos ejemplos (few-shot).
  3. Llama al modelo desplegado en Azure OpenAI para generar SQL.
  4. Valida que el SQL generado sea una sola sentencia SELECT (nunca escritura).
  5. Ejecuta la consulta contra Azure SQL DB con el usuario de solo lectura.
  6. Imprime el SQL generado y el resultado.

Uso:
    python text2sql_poc.py "Quiero la coordenada del suministro 20004016"
"""

import os
import re
import sys

import pyodbc
from dotenv import load_dotenv
from openai import AzureOpenAI

load_dotenv()

# =============================================================================
# Configuracion (leida desde .env)
# =============================================================================
AZURE_OPENAI_ENDPOINT = os.environ["AZURE_OPENAI_ENDPOINT"]
AZURE_OPENAI_KEY = os.environ["AZURE_OPENAI_KEY"]
AZURE_OPENAI_DEPLOYMENT = os.environ["AZURE_OPENAI_DEPLOYMENT"]
AZURE_OPENAI_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")

SQL_SERVER = os.environ["SQL_SERVER"]
SQL_DATABASE = os.environ["SQL_DATABASE"]
SQL_USER = os.environ["SQL_USER"]
SQL_PASSWORD = os.environ["SQL_PASSWORD"]

MAX_ROWS = 200  # limite de filas por defecto si el modelo no pone TOP

# =============================================================================
# Esquema que se le da al modelo (mantenlo sincronizado con el .sql real)
# =============================================================================
SCHEMA_CONTEXT = """
dbo.Suministro (
    SuministroID INT PK, PuntoSuministro VARCHAR, Instalacion VARCHAR,
    CuentaContrato VARCHAR, Contrato VARCHAR, InteresadoComercial VARCHAR,
    NombreCliente VARCHAR, ApellidoCliente VARCHAR, TipoTarifa VARCHAR,
    Poblacion VARCHAR, Distrito VARCHAR, CodigoPostal VARCHAR, Calle VARCHAR,
    NumeroCasa VARCHAR, Calle4Referencia VARCHAR, DS_Estado VARCHAR, Activo BIT
)
dbo.Coordenada (
    CoordenadaID INT PK, SuministroID INT FK -> Suministro,
    Latitud DECIMAL, Longitud DECIMAL, LambertX DECIMAL, LambertY DECIMAL,
    PrecisionGPS VARCHAR, FechaCaptura DATETIME
)
dbo.OrdenLectura (
    OrdenID BIGINT PK, SuministroID INT FK -> Suministro,
    Ruta VARCHAR, Secuencia VARCHAR, UnidadLectura VARCHAR, Equipo VARCHAR,
    NumeroSerie VARCHAR, FabricanteMedidor VARCHAR, TipoMedidor VARCHAR,
    FechaLecturaPlanificada DATE, FechaLecturaEfectiva DATE, HoraLectura TIME,
    StatusLectura VARCHAR, ResultadoLecturaInterno DECIMAL, NotaLectura VARCHAR,
    CreadoPor VARCHAR, FechaCreacion DATETIME
)
dbo.Croquis (
    CroquisID INT PK, SuministroID INT FK -> Suministro,
    TipoDocumento VARCHAR, RutaArchivo VARCHAR, Descripcion VARCHAR, FechaCarga DATETIME
)
dbo.Contiguo (
    ContiguoID INT PK, SuministroID INT FK -> Suministro,
    SuministroContiguoID INT FK -> Suministro, TipoRelacion VARCHAR
)
dbo.NotaCaso (
    NotaID INT PK, SuministroID INT FK -> Suministro, OrdenID BIGINT FK -> OrdenLectura,
    TipoNota VARCHAR, TextoNota VARCHAR, UsuarioRegistro VARCHAR, Rol VARCHAR, FechaRegistro DATETIME
)
dbo.CambioMedidor (
    CambioID INT PK, SuministroID INT FK -> Suministro,
    MedidorAnterior VARCHAR, MedidorNuevo VARCHAR, FechaCambio DATE,
    Motivo VARCHAR, TecnicoResponsable VARCHAR
)
dbo.Reclamo (
    ReclamoID INT PK, SuministroID INT FK -> Suministro,
    FechaReclamo DATE, TipoReclamo VARCHAR, Estado VARCHAR, Descripcion VARCHAR
)
dbo.Facturacion (
    FacturacionID INT PK, SuministroID INT FK -> Suministro,
    UltimaLecturaFacturada DECIMAL, FechaFacturacion DATE,
    ConsumoFacturado DECIMAL, MontoFacturado DECIMAL
)
dbo.IncidenciaAplicativo (
    IncidenciaID INT PK, OrdenID BIGINT FK -> OrdenLectura,
    UsuarioReporta VARCHAR, TipoIncidencia VARCHAR, Descripcion VARCHAR,
    FechaReporte DATETIME, Resuelta BIT
)
dbo.vw_HistorialLecturaSuministro (
    -- vista de solo lectura, usar para preguntas de "lectura anterior / siguiente"
    SuministroID, PuntoSuministro, NombreCliente, ApellidoCliente, OrdenID,
    FechaLecturaEfectiva, StatusLectura, ResultadoLecturaInterno, NotaLectura,
    FechaLecturaAnterior, ResultadoLecturaAnterior, StatusLecturaAnterior,
    FechaLecturaSiguiente, ResultadoLecturaSiguiente, DiasDesdeLecturaAnterior
)
""".strip()

FEW_SHOT_EXAMPLES = """
Pregunta: Quiero la coordenada del suministro 20004016
SQL: SELECT c.Latitud, c.Longitud FROM dbo.Coordenada c INNER JOIN dbo.Suministro s ON s.SuministroID = c.SuministroID WHERE s.PuntoSuministro = '20004016';

Pregunta: No me aparece este medidor en terreno, suministro 20004018
SQL: SELECT TOP 5 ol.OrdenID, ol.StatusLectura, ol.NotaLectura, ol.FechaLecturaPlanificada FROM dbo.OrdenLectura ol INNER JOIN dbo.Suministro s ON s.SuministroID = ol.SuministroID WHERE s.PuntoSuministro = '20004018' ORDER BY ol.FechaLecturaPlanificada DESC;

Pregunta: Cual es el contiguo del suministro 20004016
SQL: SELECT sc.PuntoSuministro AS SuministroContiguo, c.TipoRelacion FROM dbo.Contiguo c INNER JOIN dbo.Suministro s ON s.SuministroID = c.SuministroID INNER JOIN dbo.Suministro sc ON sc.SuministroID = c.SuministroContiguoID WHERE s.PuntoSuministro = '20004016';

Pregunta: Este suministro ha tenido reclamo, 20004018
SQL: SELECT r.FechaReclamo, r.TipoReclamo, r.Estado, r.Descripcion FROM dbo.Reclamo r INNER JOIN dbo.Suministro s ON s.SuministroID = r.SuministroID WHERE s.PuntoSuministro = '20004018';

Pregunta: Cual fue la lectura anterior del suministro 20004016
SQL: SELECT FechaLecturaEfectiva, ResultadoLecturaInterno, FechaLecturaAnterior, ResultadoLecturaAnterior FROM dbo.vw_HistorialLecturaSuministro WHERE PuntoSuministro = '20004016' ORDER BY FechaLecturaEfectiva DESC;
""".strip()

SYSTEM_PROMPT = f"""Eres un asistente que traduce preguntas en espanol de lectores, supervisores y \
verificadores de medidores a consultas T-SQL para Azure SQL Database.

Reglas estrictas:
- Responde UNICAMENTE con la sentencia SQL, sin explicaciones, sin markdown, sin ```sql.
- Genera SIEMPRE una unica sentencia SELECT. Nunca INSERT, UPDATE, DELETE, DROP, ALTER, EXEC ni MERGE.
- Usa solo las tablas y columnas listadas en el esquema. No inventes columnas.
- Si la pregunta no se puede responder con el esquema disponible, responde exactamente: NO_RESPONDIBLE
- Si el usuario no limita el resultado, agrega TOP 50 para no traer demasiadas filas.

Esquema disponible:
{SCHEMA_CONTEXT}

Ejemplos:
{FEW_SHOT_EXAMPLES}
"""

# =============================================================================
# Validacion del SQL generado (capa de seguridad, nunca confiar ciegamente en el LLM)
# =============================================================================
FORBIDDEN_KEYWORDS = (
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE",
    "EXEC", "EXECUTE", "MERGE", "GRANT", "REVOKE", "CREATE", "sp_",
)


def validar_sql(sql: str) -> str:
    """Lanza ValueError si el SQL no cumple las reglas de seguridad. Devuelve el SQL limpio."""
    sql_limpio = sql.strip().rstrip(";")

    if not sql_limpio:
        raise ValueError("El modelo no genero SQL.")

    # Debe ser una sola sentencia: no permitir un ';' seguido de mas contenido.
    partes = [p for p in sql_limpio.split(";") if p.strip()]
    if len(partes) > 1:
        raise ValueError("Se detecto mas de una sentencia SQL, se rechaza por seguridad.")

    primera_palabra = sql_limpio.strip().split(None, 1)[0].upper()
    if primera_palabra != "SELECT":
        raise ValueError(f"Solo se permiten consultas SELECT, se recibio: {primera_palabra}")

    sql_mayus = sql_limpio.upper()
    for palabra in FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{palabra}\b", sql_mayus):
            raise ValueError(f"Palabra no permitida detectada en el SQL: {palabra}")

    # Si no trae TOP ni OFFSET, le agregamos un limite por seguridad de performance.
    if "TOP " not in sql_mayus and "OFFSET " not in sql_mayus:
        sql_limpio = re.sub(
            r"^SELECT\s+", f"SELECT TOP {MAX_ROWS} ", sql_limpio, count=1, flags=re.IGNORECASE
        )

    return sql_limpio


# =============================================================================
# Llamada a Azure OpenAI
# =============================================================================
def generar_sql(pregunta: str) -> str:
    client = AzureOpenAI(
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_key=AZURE_OPENAI_KEY,
        api_version=AZURE_OPENAI_API_VERSION,
    )

    respuesta = client.chat.completions.create(
        model=AZURE_OPENAI_DEPLOYMENT,
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": pregunta},
        ],
    )

    return respuesta.choices[0].message.content.strip()


# =============================================================================
# Ejecucion contra Azure SQL DB
# =============================================================================
def ejecutar_sql(sql: str):
    conn_str = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER=tcp:{SQL_SERVER},1433;"
        f"DATABASE={SQL_DATABASE};"
        f"UID={SQL_USER};PWD={SQL_PASSWORD};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
        "Connection Timeout=90;"
    )
    with pyodbc.connect(conn_str) as conn:
        cursor = conn.cursor()
        cursor.execute(sql)
        columnas = [col[0] for col in cursor.description]
        filas = cursor.fetchall()
        return columnas, filas


# =============================================================================
# Punto de entrada
# =============================================================================
def main():
    if len(sys.argv) < 2:
        print('Uso: python text2sql_poc.py "tu pregunta aqui"')
        sys.exit(1)

    pregunta = sys.argv[1]
    print(f"\nPregunta: {pregunta}\n")

    sql_generado = generar_sql(pregunta)

    if sql_generado.strip().upper() == "NO_RESPONDIBLE":
        print("El modelo indica que no puede responder esta pregunta con el esquema disponible.")
        return

    print(f"SQL generado (sin validar):\n  {sql_generado}\n")

    try:
        sql_validado = validar_sql(sql_generado)
    except ValueError as e:
        print(f"SQL RECHAZADO por la capa de validacion: {e}")
        return

    print(f"SQL a ejecutar:\n  {sql_validado}\n")

    try:
        columnas, filas = ejecutar_sql(sql_validado)
    except pyodbc.Error as e:
        print(f"Error al ejecutar contra Azure SQL DB: {e}")
        return

    if not filas:
        print("La consulta no devolvio resultados.")
        return

    print(" | ".join(columnas))
    print("-" * 60)
    for fila in filas:
        print(" | ".join(str(v) for v in fila))


if __name__ == "__main__":
    main()
