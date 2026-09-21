"""
Interfaz de chat en Streamlit para el agente Text-to-SQL.

Esta app NO contiene logica de negocio: solo arma la conversacion, llama a
text2sql_core.responder_pregunta() por cada mensaje del usuario, y muestra
el resultado (SQL generado, tabla de resultados, consumo de tokens).

Ejecutar con:
    streamlit run app_streamlit.py
"""

import pandas as pd
import streamlit as st

from text2sql_core import responder_pregunta

st.set_page_config(page_title="Agente Text-to-SQL - Lecturas MOM/ISU", page_icon="💬", layout="wide")

st.title("💬 Agente Text-to-SQL — Lecturas de medidores")
st.caption(
    "Preguntas en lenguaje natural sobre suministros, coordenadas, croquis, "
    "contiguos, reclamos, facturacion y cambios de medidor."
)

with st.sidebar:
    st.subheader("Preguntas de ejemplo")
    ejemplos = [
        "Quiero la coordenada del suministro 20004016",
        "Quiero el contiguo del suministro 20004016",
        "No me aparece este medidor en terreno, suministro 20004018",
        "Este suministro ha tenido reclamo, 20004018",
        "Cual fue la ultima lectura facturada del suministro 20004022 y su fecha",
        "Hubo cambio de medidor en terreno para el suministro 20004021",
    ]
    for ej in ejemplos:
        if st.button(ej, use_container_width=True):
            st.session_state["pregunta_prefill"] = ej

    st.divider()
    mostrar_sql = st.toggle("Mostrar SQL generado", value=True)
    mostrar_tokens = st.toggle("Mostrar consumo de tokens", value=True)

# Historial de la conversacion en esta sesion de navegador
if "historial" not in st.session_state:
    st.session_state["historial"] = []

# Redibuja los mensajes previos
for turno in st.session_state["historial"]:
    with st.chat_message(turno["role"]):
        st.markdown(turno["content"])
        if turno.get("sql") and mostrar_sql:
            st.code(turno["sql"], language="sql")
        if turno.get("df") is not None:
            st.dataframe(turno["df"], use_container_width=True)
        if turno.get("tokens") and mostrar_tokens:
            t = turno["tokens"]
            st.caption(
                f"Tokens -> prompt: {t.get('prompt_tokens', 0)} | "
                f"respuesta: {t.get('completion_tokens', 0)} | "
                f"total: {t.get('total_tokens', 0)}"
            )

# Entrada del chat (toma el prefill del sidebar si se hizo click en un ejemplo)
prefill = st.session_state.pop("pregunta_prefill", None)
pregunta_usuario = st.chat_input("Escribe tu pregunta...") or None

if prefill and not pregunta_usuario:
    pregunta_usuario = prefill

if pregunta_usuario:
    st.session_state["historial"].append({"role": "user", "content": pregunta_usuario})
    with st.chat_message("user"):
        st.markdown(pregunta_usuario)

    with st.chat_message("assistant"):
        with st.spinner("Generando SQL y consultando la base..."):
            resultado = responder_pregunta(pregunta_usuario)

        turno_asistente = {"role": "assistant", "content": "", "tokens": resultado.uso_tokens}

        if resultado.no_respondible:
            texto = "No puedo responder esta pregunta con las tablas disponibles en el esquema."
            st.warning(texto)
            turno_asistente["content"] = texto

        elif not resultado.ok:
            texto = f"Ocurrio un problema: {resultado.error}"
            st.error(texto)
            turno_asistente["content"] = texto
            if resultado.sql_generado:
                turno_asistente["sql"] = resultado.sql_generado

        else:
            if not resultado.filas:
                texto = "La consulta se ejecuto correctamente pero no devolvio resultados."
                st.info(texto)
                turno_asistente["content"] = texto
            else:
                texto = f"Encontre {len(resultado.filas)} fila(s)."
                st.markdown(texto)
                df = pd.DataFrame([list(f) for f in resultado.filas], columns=resultado.columnas)
                st.dataframe(df, use_container_width=True)
                turno_asistente["content"] = texto
                turno_asistente["df"] = df

            turno_asistente["sql"] = resultado.sql_ejecutado or resultado.sql_generado

        if mostrar_sql and turno_asistente.get("sql"):
            st.code(turno_asistente["sql"], language="sql")

        if mostrar_tokens and resultado.uso_tokens:
            t = resultado.uso_tokens
            st.caption(
                f"Tokens -> prompt: {t.get('prompt_tokens', 0)} | "
                f"respuesta: {t.get('completion_tokens', 0)} | "
                f"total: {t.get('total_tokens', 0)}"
            )

    st.session_state["historial"].append(turno_asistente)