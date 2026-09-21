"""
Nucleo del agente Text-to-SQL: toda la logica reutilizable, sin interfaz.
 
Este modulo NO sabe si quien lo llama es una terminal, un chat de Streamlit,
un endpoint de FastAPI, o un agente lider en un sistema multiagente. Esa
separacion es intencional: cuando mas adelante quieras exponer este agente
a un "jardin de agentes" (Azure AI Foundry Connected Agents, un servidor
MCP, o una API propia), solo agregas una puerta de entrada nueva que
importe y llame a `responder_pregunta()` — la logica de generar SQL,
validarlo y ejecutarlo no cambia.
 
Uso directo (sin interfaz):
    from text2sql_core import responder_pregunta
    resultado = responder_pregunta("Quiero la coordenada del suministro 20004016")
"""

import os
import re
from dataclasses import dataclass, field

import pyodbc
from dotenv import load_dotenv
from openai import AzureOpenAI

load_dotenv()  # Carga las variables de entorno desde el archivo .env

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
 
    if "TOP " not in sql_mayus and "OFFSET " not in sql_mayus:
        sql_limpio = re.sub(
            r"^SELECT\s+", f"SELECT TOP {MAX_ROWS} ", sql_limpio, count=1, flags=re.IGNORECASE
        )
 
    return sql_limpio
 
 
# =============================================================================
# Llamada a Azure OpenAI
# =============================================================================
def generar_sql(pregunta: str) -> tuple[str, dict]:
    """Devuelve (sql_generado, uso_tokens)."""
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
 
    uso = respuesta.usage
    uso_dict = {
        "prompt_tokens": uso.prompt_tokens,
        "completion_tokens": uso.completion_tokens,
        "total_tokens": uso.total_tokens,
    }
 
    return respuesta.choices[0].message.content.strip(), uso_dict
 
 
# =============================================================================
# Ejecucion contra Azure SQL DB
# =============================================================================
def ejecutar_sql(sql: str) -> tuple[list[str], list[tuple]]:
    conn_str = (
        "DRIVER={ODBC Driver 18 for SQL Server};"
        f"SERVER=tcp:{SQL_SERVER},1433;"
        f"DATABASE={SQL_DATABASE};"
        f"UID={SQL_USER};PWD={SQL_PASSWORD};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
    )
    with pyodbc.connect(conn_str) as conn:
        cursor = conn.cursor()
        cursor.execute(sql)
        columnas = [col[0] for col in cursor.description]
        filas = cursor.fetchall()
        return columnas, filas
 
 
# =============================================================================
# API publica del modulo: una sola funcion que cualquier interfaz puede llamar
# =============================================================================
@dataclass
class RespuestaAgente:
    """Resultado estructurado de una pregunta. Pensado para que tanto un humano
    (via Streamlit) como otro agente (via API/Connected Agent) puedan consumirlo."""
 
    pregunta: str
    ok: bool
    sql_generado: str | None = None
    sql_ejecutado: str | None = None
    columnas: list[str] = field(default_factory=list)
    filas: list[tuple] = field(default_factory=list)
    error: str | None = None
    no_respondible: bool = False
    uso_tokens: dict = field(default_factory=dict)
 
    def to_dict(self) -> dict:
        """Formato serializable en JSON — el 'contrato' que usaria un agente lider."""
        return {
            "pregunta": self.pregunta,
            "ok": self.ok,
            "sql_generado": self.sql_generado,
            "sql_ejecutado": self.sql_ejecutado,
            "columnas": self.columnas,
            "filas": [list(f) for f in self.filas],
            "error": self.error,
            "no_respondible": self.no_respondible,
            "uso_tokens": self.uso_tokens,
        }
 
 
def responder_pregunta(pregunta: str) -> RespuestaAgente:
    """Punto de entrada unico del agente: pregunta en lenguaje natural -> resultado.
 
    Esta es la funcion que llamaria Streamlit, un endpoint de FastAPI, o un
    agente lider en una arquitectura multiagente. No imprime nada, no
    depende de consola ni de UI: solo devuelve un objeto con el resultado
    o el motivo del fallo.
    """
    try:
        sql_generado, uso_tokens = generar_sql(pregunta)
    except Exception as e:  # noqa: BLE001 - queremos capturar cualquier fallo del cliente OpenAI
        return RespuestaAgente(pregunta=pregunta, ok=False, error=f"Error al generar SQL: {e}")
 
    if sql_generado.strip().upper() == "NO_RESPONDIBLE":
        return RespuestaAgente(
            pregunta=pregunta,
            ok=False,
            no_respondible=True,
            sql_generado=sql_generado,
            uso_tokens=uso_tokens,
            error="El modelo indica que no puede responder esta pregunta con el esquema disponible.",
        )
 
    try:
        sql_validado = validar_sql(sql_generado)
    except ValueError as e:
        return RespuestaAgente(
            pregunta=pregunta,
            ok=False,
            sql_generado=sql_generado,
            uso_tokens=uso_tokens,
            error=f"SQL rechazado por la capa de validacion: {e}",
        )
 
    try:
        columnas, filas = ejecutar_sql(sql_validado)
    except pyodbc.Error as e:
        return RespuestaAgente(
            pregunta=pregunta,
            ok=False,
            sql_generado=sql_generado,
            sql_ejecutado=sql_validado,
            uso_tokens=uso_tokens,
            error=f"Error al ejecutar contra Azure SQL DB: {e}",
        )
 
    return RespuestaAgente(
        pregunta=pregunta,
        ok=True,
        sql_generado=sql_generado,
        sql_ejecutado=sql_validado,
        columnas=columnas,
        filas=filas,
        uso_tokens=uso_tokens,
    )