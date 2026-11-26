"""Lector de facturas: sube fotos o PDFs y descarga los datos en Excel.

Funciona sin APIs de pago: lee el texto de PDFs digitales y usa OCR (Tesseract)
para fotos y PDFs escaneados.

Ejecutar con:  streamlit run app.py
"""
from datetime import datetime

import pandas as pd
import streamlit as st

from excel import generar_excel
from extractor import ErrorExtraccion, extraer_factura

st.set_page_config(page_title="Lector de facturas", page_icon="🧾", layout="wide")
st.title("🧾 Lector de facturas")
st.caption("Sube fotos o PDFs de facturas y obtén sus datos en un Excel.")

with st.sidebar:
    st.header("Consejos")
    st.markdown(
        "- **PDF original** (no escaneado): mejor resultado.\n"
        "- **Fotos:** buena luz, documento plano, sin sombras y de frente.\n"
        "- Revisa siempre los datos antes de usarlos: la lectura es automática.\n\n"
        "**Formatos:** PDF, JPG, PNG, WEBP"
    )

archivos = st.file_uploader(
    "Arrastra aquí tus facturas",
    type=["pdf", "jpg", "jpeg", "png", "webp"],
    accept_multiple_files=True,
)

if "resultados" not in st.session_state:
    st.session_state.resultados = []

if st.button("Extraer datos", type="primary", disabled=not archivos):
    resultados, errores = [], []
    progreso = st.progress(0.0, text="Procesando…")
    for i, archivo in enumerate(archivos):
        progreso.progress(i / len(archivos), text=f"Leyendo {archivo.name}…")
        try:
            resultados.append((archivo.name, extraer_factura(archivo.getvalue(), archivo.type)))
        except ErrorExtraccion as e:
            errores.append(f"{archivo.name}: {e}")
        except Exception as e:  # archivo dañado, formato no soportado, etc.
            errores.append(f"{archivo.name}: no se pudo leer ({e})")
    progreso.empty()
    st.session_state.resultados = resultados
    for err in errores:
        st.error(err)


def _monto(v):
    return f"{v:,.2f}" if v is not None else "—"


resultados = st.session_state.resultados
if resultados:
    st.success(f"{len(resultados)} factura(s) procesada(s).")
    st.download_button(
        "⬇️ Descargar Excel",
        data=generar_excel(resultados),
        file_name=f"facturas_{datetime.now():%Y%m%d_%H%M}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
    )

    for nombre, f in resultados:
        titulo = f"{nombre} — {f.tipo_documento or 'Documento'} {f.numero_factura or ''}"
        with st.expander(titulo, expanded=len(resultados) == 1):
            st.caption(f"Lectura: {f.metodo_lectura} · Confianza: {f.confianza}")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Fecha", f.fecha_emision or "—")
            c2.metric("Subtotal", _monto(f.subtotal))
            c3.metric("Impuestos", _monto(sum(i.monto or 0 for i in f.impuestos)) if f.impuestos else "—")
            c4.metric(f"Total {f.moneda or ''}", _monto(f.total))

            e, r = st.columns(2)
            e.markdown("**Emisor**")
            e.json(f.emisor.model_dump(exclude_none=True))
            r.markdown("**Receptor**")
            r.json(f.receptor.model_dump(exclude_none=True))

            if f.items:
                st.markdown("**Ítems**")
                st.dataframe(pd.DataFrame([i.model_dump() for i in f.items]), use_container_width=True)
            if f.impuestos:
                st.markdown("**Impuestos**")
                st.dataframe(pd.DataFrame([i.model_dump() for i in f.impuestos]), use_container_width=True)
            if f.datos_adicionales:
                st.markdown("**Otros datos**")
                st.dataframe(pd.DataFrame([d.model_dump() for d in f.datos_adicionales]), use_container_width=True)
            with st.popover("Ver texto completo leído"):
                st.text(f.texto_completo)
            if f.confianza == "baja":
                st.warning("Se encontraron pocos datos: revisa el documento o prueba con una imagen más nítida.")
