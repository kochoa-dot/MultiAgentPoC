import json
import os
import subprocess
import sys
import unittest
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import sqlglot

import text2sql_core as core


CFG = core.Configuracion("lecturas", "vw_chatbot_mom")
VISTA = CFG.nombre_sql
SELECT = f"SELECT v.[Pue.sumin.], v.[Equipo] FROM {VISTA} AS v"


class ValidacionSQLTests(unittest.TestCase):
    def test_ejemplos_soportados(self):
        for pregunta, sql in core.ejemplos_sql(CFG):
            if sql != "NO_RESPONDIBLE":
                with self.subTest(pregunta=pregunta):
                    validado = core.validar_sql(sql, CFG)
                    self.assertEqual(len(sqlglot.parse(validado, read="tsql")), 1)

    def test_limite_predeterminado(self):
        self.assertIn("TOP 50", core.validar_sql(SELECT, CFG))

    def test_limite_excesivo(self):
        sql = SELECT.replace("SELECT ", "SELECT TOP 999999 ", 1)
        self.assertIn("TOP 200", core.validar_sql(sql, CFG))

    def test_conserva_limite_menor(self):
        self.assertIn("TOP 1 ", core.validar_sql(SELECT.replace("SELECT ", "SELECT TOP 1 ", 1), CFG))

    def test_limite_configurable(self):
        cfg = core.Configuracion("lecturas", "vw_chatbot_mom", max_filas=30, filas_por_defecto=10)
        self.assertIn("TOP 10", core.validar_sql(SELECT, cfg))

    def test_preserva_literal_y_ceros(self):
        sql = SELECT + " WHERE v.[Pue.sumin.] = '0020009838' AND v.[Equipo] = 'DROP; DELETE'"
        limpio = core.validar_sql(sql, CFG)
        self.assertIn("'0020009838'", limpio)
        self.assertIn("'DROP; DELETE'", limpio)

    def test_alias_en_order_by(self):
        sql = f"SELECT COUNT(DISTINCT [Pue.sumin.]) AS Cantidad FROM {VISTA} ORDER BY Cantidad DESC"
        self.assertIn("Cantidad", core.validar_sql(sql, CFG))

    def test_count_filas_no_exige_columna_ficticia(self):
        self.assertIn("COUNT(*)", core.validar_sql(f"SELECT COUNT(*) AS Filas FROM {VISTA}", CFG))

    def test_comentarios_se_eliminan(self):
        self.assertNotIn("oculto", core.validar_sql(SELECT + " -- oculto", CFG))

    def test_rechazos_de_objetos_y_sentencias(self):
        casos = [
            "", "NO_RESPONDIBLE", "```sql\n" + SELECT + "\n```",
            SELECT + "; DELETE FROM " + VISTA,
            f"DELETE FROM {VISTA}", f"UPDATE {VISTA} SET [Equipo]='x'",
            f"SELECT [Equipo] INTO otra FROM {VISTA}",
            "SELECT [Equipo] FROM dbo.OrdenLectura",
            "SELECT [Equipo] FROM dbo.vw_chatbot_mom",
            "SELECT [Equipo] FROM vw_chatbot_mom",
            "SELECT [Equipo] FROM otra_bd.lecturas.vw_chatbot_mom",
            f"SELECT * FROM {VISTA}",
            f"SELECT v.* FROM {VISTA} AS v",
            f"SELECT [Calle 4] FROM {VISTA}",
            f"SELECT [Latitud] FROM {VISTA}",
            f"SELECT [FechaFacturacion] FROM {VISTA}",
            f"SELECT x.[Equipo] FROM {VISTA} AS v",
            f"SELECT [Equipo] FROM {VISTA} UNION SELECT [Equipo] FROM {VISTA}",
            f"SELECT v.[Equipo] FROM {VISTA} v JOIN {VISTA} x ON v.[Equipo]=x.[Equipo]",
            f"SELECT [Equipo] FROM {VISTA} WHERE EXISTS (SELECT 1 FROM sys.tables)",
            f"SELECT [Equipo], (SELECT TOP 1 [Equipo] FROM {VISTA}) FROM {VISTA}",
            f"WITH x AS (SELECT [Equipo] FROM {VISTA}) SELECT [Equipo] FROM x",
            f"SELECT TOP 5 PERCENT [Equipo] FROM {VISTA}",
            f"SELECT TOP 5 WITH TIES [Equipo] FROM {VISTA} ORDER BY [Equipo]",
            f"SELECT TOP (@cantidad) [Equipo] FROM {VISTA}",
            f"SELECT TOP 0 [Equipo] FROM {VISTA}",
            f"SELECT [Equipo] FROM {VISTA} ORDER BY [Equipo] OFFSET 0 ROWS FETCH NEXT 1000 ROWS ONLY",
            f"SELECT [Equipo] FROM {VISTA} WITH (NOLOCK)",
            f"SELECT [Equipo], dbo.funcion_privada() FROM {VISTA}",
            "SELECT [Equipo] FROM OPENROWSET('x','x','x')",
            f"SELECT [Equipo], @@VERSION FROM {VISTA}",
            f"SELECT [Equipo], SUSER_SNAME() FROM {VISTA}",
            f"SELECT [Equipo] AS Oculto FROM {VISTA} WHERE Oculto='x'",
        ]
        for sql in casos:
            with self.subTest(sql=sql):
                with self.assertRaises(ValueError):
                    core.validar_sql(sql, CFG)


class ContratoTests(unittest.TestCase):
    @patch.object(core, "ejecutar_sql")
    @patch.object(core, "generar_sql", return_value=(SELECT, {"total_tokens": 20}))
    def test_solo_genera_por_defecto(self, generar, ejecutar):
        r = core.responder_pregunta("equipo", config=CFG)
        self.assertTrue(r.ok)
        self.assertFalse(r.ejecutado)
        self.assertIsNone(r.sql_ejecutado)
        self.assertIn("TOP 50", r.sql_validado)
        ejecutar.assert_not_called()

    @patch.object(core, "ejecutar_sql", return_value=(["Equipo"], [("0001",)]))
    @patch.object(core, "generar_sql", return_value=(SELECT, {}))
    def test_ejecucion_explicita(self, generar, ejecutar):
        r = core.responder_pregunta("equipo", ejecutar=True, config=CFG)
        self.assertTrue(r.ok and r.ejecutado)
        self.assertEqual(r.sql_ejecutado, r.sql_validado)
        self.assertEqual(r.filas, [("0001",)])

    @patch.object(core, "ejecutar_sql")
    @patch.object(core, "generar_sql", return_value=("NO_RESPONDIBLE", {}))
    def test_sin_fuente_no_ejecuta(self, generar, ejecutar):
        r = core.responder_pregunta("reclamos", ejecutar=True, config=CFG)
        self.assertTrue(r.no_respondible)
        self.assertFalse(r.ok)
        ejecutar.assert_not_called()

    @patch.object(core, "ejecutar_sql")
    @patch.object(core, "generar_sql", return_value=("SELECT * FROM dbo.Suministro", {}))
    def test_no_ejecuta_sql_rechazado(self, generar, ejecutar):
        r = core.responder_pregunta("equipo", ejecutar=True, config=CFG)
        self.assertFalse(r.ok)
        ejecutar.assert_not_called()

    @patch.object(core, "generar_sql", side_effect=RuntimeError("Password=SECRETO; host=PRIVADO"))
    def test_error_modelo_no_expone_detalles(self, generar):
        r = core.responder_pregunta("equipo", config=CFG)
        self.assertFalse(r.ok)
        self.assertNotIn("SECRETO", json.dumps(r.to_dict()))
        self.assertNotIn("PRIVADO", json.dumps(r.to_dict()))

    @patch.object(core, "ejecutar_sql", side_effect=RuntimeError("Password=SECRETO;"))
    @patch.object(core, "generar_sql", return_value=(SELECT, {}))
    def test_error_bd_no_expone_detalles(self, generar, ejecutar):
        r = core.responder_pregunta("equipo", ejecutar=True, config=CFG)
        self.assertFalse(r.ok)
        self.assertFalse(r.ejecutado)
        self.assertIsNone(r.sql_ejecutado)
        self.assertNotIn("SECRETO", json.dumps(r.to_dict()))

    def test_serializacion_tipos_bd(self):
        r = core.RespuestaAgente("p", True, filas=[(Decimal("123.4500"), datetime(2026, 10, 6), b"x")])
        resultado = json.loads(json.dumps(r.to_dict()))
        self.assertEqual(resultado["filas"][0], ["123.4500", "2026-10-06T00:00:00", {"base64": "eA=="}])

    def test_importacion_sin_configuracion(self):
        entorno = {k: v for k, v in os.environ.items() if not k.startswith(("SQL_", "AZURE_OPENAI_"))}
        p = subprocess.run([sys.executable, "-c", "import text2sql_core; print('ok')"],
                           env=entorno, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_prompt_no_contiene_tablas_demo(self):
        prompt = core.construir_prompt(CFG)
        self.assertIn(VISTA, prompt)
        for tabla in ("dbo.Suministro", "dbo.Coordenada", "dbo.Reclamo", "dbo.NotaCaso"):
            self.assertNotIn(tabla, prompt)
        self.assertEqual(len(set(core.COLUMNAS_VISTA)), 58)

    @patch.dict(os.environ, {"SQL_ENGINE": "oracle", "SQL_VIEW_SCHEMA": "x", "SQL_VIEW_NAME": "v"})
    def test_otro_motor_se_rechaza(self):
        with self.assertRaises(ValueError):
            core.Configuracion.desde_entorno()

    @patch.dict(os.environ, {"SQL_ENGINE": "sqlserver", "SQL_VIEW_SCHEMA": "", "SQL_VIEW_NAME": "v"})
    def test_no_asume_dbo(self):
        with self.assertRaises(ValueError):
            core.Configuracion.desde_entorno()

    def test_config_invalida(self):
        for args in [("", "v"), ("x", "v", 10, 50), ("x", "v", 1001, 50), ("x", "v", 200, 50, 0)]:
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    core.Configuracion(*args)


class BaseDeDatosTests(unittest.TestCase):
    @patch.object(core, "_conectar")
    def test_valida_antes_de_conectar(self, conectar):
        with self.assertRaises(ValueError):
            core.ejecutar_sql("DROP TABLE x", CFG)
        conectar.assert_not_called()

    @patch.object(core, "_conectar")
    def test_metadatos_parametrizados(self, conectar):
        cursor = conectar.return_value.cursor.return_value
        cursor.fetchmany.return_value = [("Equipo", "varchar", 30, 0, 0, True)]
        datos = core.consultar_metadatos(CFG)
        consulta, esquema, vista = cursor.execute.call_args.args
        self.assertIn("s.name=? AND v.name=?", consulta)
        self.assertEqual((esquema, vista), (CFG.esquema, CFG.vista))
        self.assertEqual(datos[0]["nombre"], "Equipo")
        cursor.close.assert_called_once()
        conectar.return_value.close.assert_called_once()

    @patch.object(core, "_metadatos_en_conexion", return_value=[])
    @patch.object(core, "_conectar")
    def test_columna_ausente_en_vista_real(self, conectar, metadatos):
        with self.assertRaises(ValueError):
            core.ejecutar_sql(SELECT, CFG)
        conectar.return_value.close.assert_called_once()

    @patch.object(core, "_metadatos_en_conexion", return_value=[])
    @patch.object(core, "_conectar")
    def test_alias_no_oculta_columna_fuente_ausente(self, conectar, metadatos):
        with self.assertRaises(ValueError):
            core.ejecutar_sql(f"SELECT [Equipo] AS [Equipo] FROM {VISTA}", CFG)

    @patch.object(core, "_metadatos_en_conexion", return_value=[{"nombre": "Equipo"}, {"nombre": "Pue.sumin."}])
    @patch.object(core, "_conectar")
    def test_lectura_limitada_y_cierre(self, conectar, metadatos):
        cursor = conectar.return_value.cursor.return_value
        cursor.description = [("Pue.sumin.",), ("Equipo",)]
        cursor.fetchmany.return_value = [("0020009838", "0001")]
        columnas, filas = core.ejecutar_sql(SELECT, CFG)
        self.assertEqual(columnas, ["Pue.sumin.", "Equipo"])
        self.assertEqual(filas[0][0], "0020009838")
        cursor.fetchmany.assert_called_once_with(200)
        cursor.close.assert_called_once()
        conectar.return_value.close.assert_called_once()

    @patch.object(core, "_metadatos_en_conexion", return_value=[{"nombre": "Equipo"}, {"nombre": "Pue.sumin."}])
    @patch.object(core, "_conectar")
    def test_cierre_tras_fallo(self, conectar, metadatos):
        cursor = conectar.return_value.cursor.return_value
        cursor.execute.side_effect = RuntimeError("fallo")
        with self.assertRaises(RuntimeError):
            core.ejecutar_sql(SELECT, CFG)
        cursor.close.assert_called_once()
        conectar.return_value.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
