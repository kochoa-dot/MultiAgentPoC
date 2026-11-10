# Text-to-SQL sobre la vista MOM

Adaptación de `text2sql_core.py` a las 58 columnas del CSV `vw_chatbot_mom_202611060936.csv` y las consultas del documento `Requerimiento chat bot v2.docx`. El Excel se usó como referencia de negocio; sus encabezados no sustituyen los del CSV real.

## Qué cambia

- La única fuente permitida es la vista configurada, con esquema explícito. Se eliminan las tablas demo Suministro, Coordenada, OrdenLectura, etc.
- Por defecto `responder_pregunta()` genera y valida SQL, sin ejecutarlo. Para consultar datos hay que indicar `ejecutar=True` o usar `--ejecutar`.
- Se conservan las funciones públicas `generar_sql`, `validar_sql`, `ejecutar_sql`, `responder_pregunta` y `RespuestaAgente.to_dict`. Se agregan `sql_validado` y `ejecutado` al resultado. Una UI que antes esperaba filas automáticamente debe adaptarse a este cambio.
- Las variables SQL no son obligatorias al importar ni para generar SQL. Solo se requieren al consultar metadatos o ejecutar.
- Se analizan sentencias y objetos mediante SQLGlot, se restringen columnas y funciones, y se aplica un TOP máximo real de 200 (configurable hasta 1000). El alcance es un SELECT simple sin JOIN, CTE, UNION, subconsultas, OFFSET o funciones arbitrarias.
- Se consultan metadatos reales antes de ejecutar; no se deducen tipos SQL a partir del CSV.
- Los errores de conexión/SDK no se entregan literalmente al usuario. Fechas y Decimal se serializan a JSON; Decimal se entrega como cadena para conservar precisión.

## Datos que todavía debes confirmar

1. Motor real. El archivo original usa SQL Server/Azure SQL y esta adaptación conserva T-SQL/ODBC. El CSV no identifica el motor. Si la vista está en Oracle, PostgreSQL u otro motor, esta versión NO debe usarse para conectar: hay que ajustar dialecto, controlador y catálogo de metadatos.
2. Nombre de base, esquema y vista. `vw_chatbot_mom` se deduce del nombre del CSV; no confirma el nombre completo. `dbo` no se asume.
3. Tipos, nulabilidad y cobertura temporal de la vista: ejecutar la consulta de metadatos o solicitar el DDL al DBA. Los 10 registros exportados no son el histórico completo.
4. Qué par de coordenadas debe ser prioritario: X/Y o DS latitud/DS longitud. Mientras se confirma, una pregunta general devuelve ambos pares con sus nombres originales.
5. Si `DS placa` corresponde al dato que el negocio pide como 'DS' o al transformador; no se afirma esa equivalencia.
6. Regla para suministros sin ceros iniciales. El script conserva los códigos recibidos, sin inventar una longitud fija ni quitar ceros.
7. Qué identifica un medidor/registro de forma única, si hay varios KWH por equipo y cómo resolver empates de fecha/hora.
8. Catálogo aprobado de notas y datos adicionales de facturación, croquis, contigüidad, reclamos y cambios si se necesitan esas consultas.

## Cobertura del requerimiento

| Consulta | Campos reales / comportamiento | Alcance |
|---|---|---|
| Coordenadas de suministro | Pue.sumin.; X; Y; DS latitud; DS longitud | Disponible; devuelve ambos pares hasta confirmar cuál es la fuente oficial |
| Dirección | Poblacion; Distrito; Calle; No. | Disponible; no hay Calle 4 en el CSV |
| Datos del medidor | Equipo; Numero de serie; Fabricante del activo fijo; Denominacion de tipo; Ubicacion del aparato | Disponible; no confirma la situación física en terreno |
| Orden, ruta, secuencia y unidad de lectura | MOB Orden; Ruta; Secuencia; Un.lect.; mru | Disponible |
| Lecturas registradas y estado | Resultado de lectura interno; FeLectEfec; HoraLect; UM; Num.; SL; Status lectura | Disponible; distinguir KWH, KW, FP y registro de medición |
| Última lectura registrada | Ordenar por fecha/hora disponibles, con unidad explícita | Solo la última disponible, no acredita la última facturada |
| Notas existentes | NotaL; Desc NotaL; Leer texto | Consulta de registros, no recomendación de códigos |
| Nombre / contrato / tarifa | Nombre1/Apellido; Nombre2/Apellido; Contrato; Cta.contrato; Texto para tipo de tarifa | Disponible; no tratar los nombres como una división segura nombre/apellido |
| Subestación/circuito/placa DS | Subestacion; Circuito; DS placa | Disponible por nombre explícito; 'DS' solo es ambiguo |
| Croquis/fotos/documentos | Sin campos de archivos | NO_RESPONDIBLE |
| Contiguo físico | Sin relación explícita entre suministros | NO_RESPONDIBLE; secuencia no prueba vecindad física |
| Última lectura facturada / fecha de factura | Sin datos confirmados de facturación | NO_RESPONDIBLE; Fact.calculo no los sustituye |
| Reclamos | Sin historial de reclamos | NO_RESPONDIBLE; no afirmar que no existen |
| Cambio de medidor | Sin eventos de cambio | NO_RESPONDIBLE; una serie actual no prueba un cambio |
| Problemas del aplicativo | Sin telemetría ni catálogo de soluciones | NO_RESPONDIBLE |
| Qué nota colocar | Sin reglas ni catálogo aprobado | NO_RESPONDIBLE |

La muestra tiene **58 columnas, 10 filas y 8 órdenes/suministros distintos**. Una orden tiene registros FP, KW y KWH. No deben contarse como tres suministros ni sumarse entre sí. Los nombres y valores del CSV solo se usan para conocer la estructura; no se envía el archivo de clientes al modelo. El prompt contiene un identificador de ejemplo y ningún nombre de cliente.

En X/Y se observan valores compatibles con latitud/longitud en grados; no son los valores Lambert del Excel demo. No debe aplicarse la interpretación Lambert de ese archivo a estas columnas sin confirmación. `FeLectEfec` no es fecha de facturación, `Lote` no confirma fecha de lectura y `Ciclo datos tecnicos` no confirma ciclo de facturación.

## Instalación y configuración local (PowerShell)

Requiere Python 3.10 o posterior. La ejecución con pyodbc requiere el controlador ODBC corporativo, por ejemplo ODBC Driver 18 for SQL Server.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edita `.env`: configura el esquema y la vista confirmados, el despliegue y las credenciales de Azure OpenAI. No compartas `.env` ni lo subas al repositorio. La versión de API conserva la del script original; valida su compatibilidad con tu despliegue corporativo. No se fija `temperature`, para no imponerlo a despliegues que no aceptan ese parámetro.

Generar SQL sin conectarse a la base:

```powershell
.\.venv\Scripts\python.exe text2sql_core.py "Quiero las coordenadas disponibles del suministro 0020009838"
```

Consultar primero los metadatos reales con una cuenta autorizada:

```powershell
.\.venv\Scripts\python.exe text2sql_core.py --metadatos
```

Solo después de confirmar motor, esquema, vista, permisos y tipos, ejecutar:

```powershell
.\.venv\Scripts\python.exe text2sql_core.py "Datos del medidor del suministro 0020009838" --ejecutar
```

Desde otra interfaz:

```python
from text2sql_core import responder_pregunta

resultado = responder_pregunta("Datos del medidor del suministro 0020009838")
print(resultado.to_dict())  # SQL generado/validado; ejecutado=False

# Ejecución opcional, en un backend autorizado y con cuenta de solo lectura:
# resultado = responder_pregunta(pregunta, ejecutar=True)
```

Los ejemplos comparan identificadores entre comillas para preservar ceros. Los tipos físicos deben confirmarse; una columna numérica en la BD puede haber perdido ya esos ceros. Las fechas se convierten con estilo 112 para admitir YYYYMMDD y fechas SQL. Confirma si hay formatos mixtos, valores especiales, horas no normalizadas o nulos antes de validar consultas históricas en producción.

## Instrucciones para el agente de Foundry

Este cambio adapta el núcleo local; no crea ni publica un recurso de Foundry ni cambia tu suscripción. Puedes obtener las mismas instrucciones con los nombres reales configurados:

```powershell
.\.venv\Scripts\python.exe text2sql_core.py --exportar-prompt instrucciones_foundry.txt
```

El archivo de instrucciones incluido tiene `REEMPLAZAR_ESQUEMA` como marcador. Regénéralo después de configurar `.env` antes de usarlo en Foundry. Crea un Prompt agent con esas instrucciones y tu modelo desplegado. En esta fase solo genera SQL; no le des una herramienta para ejecutar todavía. La validación AST continúa siendo código del backend; copiar el prompt no instala ese validador en Foundry.

Cuando se agreguen herramientas, `get_database_schema` debe recuperar solo metadatos autorizados y `execute_readonly_query` debe validar de nuevo el SQL y el alcance de datos del usuario. Registrar una función no hospeda automáticamente el código Python. El cambio a agentes no necesita recrear tablas ni entrenar un modelo con registros de clientes.

## Permisos y límites

La validación del SQL y un TOP no sustituyen los permisos en BD. La cuenta de ejecución debe tener acceso de lectura exclusivamente sobre la vista autorizada, sin roles amplios ni permisos de escritura. Si hay restricciones por lector, ruta o área, el backend y/o políticas de filas de la BD deben aplicarlas usando una identidad validada; el prompt y el texto del usuario no son autorización. Esta entrega no implementa el sistema de usuarios, menús, WhatsApp, auditoría ni seguridad por filas del documento.

El límite de filas no limita necesariamente el trabajo interno de la consulta (por ejemplo una agregación), por lo que se configura timeout y se necesita revisar el rendimiento de la vista. La corrección sintáctica no garantiza que una respuesta del modelo sea semánticamente correcta. No hay reintentos SQL automáticos ni consultas de reparación con otras tablas.

## Verificación

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s pruebas -v
```

Las pruebas locales comprueban el contrato, columnas autorizadas, límites, ataques SQL comunes, cierre de conexiones y el flujo con dobles de Azure/BD. No llaman servicios corporativos ni acreditan calidad real del modelo. Antes de conectar una aplicación, se requieren pruebas con consultas revisadas por negocio, la vista real, usuarios y permisos reales.
