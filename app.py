"""Lector de facturas: sube fotos o PDFs y descarga los datos en Excel.

Lee gratis con OCR (texto de PDFs digitales y Tesseract para fotos) y, si los datos
no cuadran, usa la IA Google Gemini. La API key va en los Secrets de Streamlit.

Ejecutar con:  streamlit run app.py
"""
import base64
import os
from datetime import datetime
from pathlib import Path


import pandas as pd
import streamlit as st

from excel import generar_excel
from extractor import ErrorExtraccion
from ia import MODOS, Uso, crear_cliente, procesar

RAIZ = Path(__file__).parent
MORADO, VERDE = "#2E1E7E", "#66CCA1"


def imagen(nombre: str) -> Path | None:
    """Busca la imagen en assets/ o, si se subió suelta, en la raíz del repositorio."""
    for ruta in (RAIZ / "assets" / nombre, RAIZ / nombre):
        if ruta.is_file():
            return ruta
    return None


LOGO, LOGO_BLANCO, ICONO = imagen("logo.png"), imagen("logo_blanco.png"), imagen("icono.png")

st.set_page_config(page_title="Lector de facturas · Respaldo Tributario",
                   page_icon=str(ICONO) if ICONO else "🧾", layout="wide")

# La tipografía y los colores base están en .streamlit/config.toml
st.markdown(f"""
<style>
.rt-header {{ display:flex; align-items:center; gap:1.25rem; flex-wrap:wrap;
    padding-bottom:1rem; margin-bottom:1.5rem; border-bottom:4px solid {VERDE}; }}
.rt-header img {{ height:64px; width:auto; max-width:100%; }}
.rt-header .rt-tit {{ font-weight:700; font-size:1.6rem; color:{MORADO}; line-height:1.1;
    padding-left:1.25rem; border-left:2px solid #D9D4EE; }}
.rt-header .rt-sub {{ font-size:1rem; color:#5B5480; font-weight:400; }}
@media (max-width: 640px) {{
    .rt-header img {{ height:44px; }}
    .rt-header .rt-tit {{ border-left:none; padding-left:0; font-size:1.3rem; }}
}}
[data-testid="stMetricValue"] {{ color:{MORADO}; font-weight:700; }}
[data-testid="stFileUploaderDropzone"] {{ border:2px dashed {VERDE}; background:#F6FBF9; }}
</style>
""", unsafe_allow_html=True)

if LOGO:
    logo_html = (f'<img src="data:image/png;base64,{base64.b64encode(LOGO.read_bytes()).decode()}" '
                 'alt="Respaldo Tributario">')
else:  # sin archivo de logo: el nombre de la marca en texto
    logo_html = (f'<div style="font-weight:700;font-size:1.9rem;line-height:1;color:{MORADO}">'
                 'RESPALDO<br>TRIBUTARIO</div>')
st.markdown(f"""
<div class="rt-header">
  {logo_html}
  <div class="rt-tit">Lector de facturas
    <div class="rt-sub">Sube fotos o PDFs de facturas y obtén sus datos en un Excel.</div>
  </div>
</div>
""", unsafe_allow_html=True)

if LOGO_BLANCO:
    st.logo(str(LOGO_BLANCO), size="large", icon_image=str(ICONO or LOGO_BLANCO))

def _api_key() -> str | None:
    try:
        if "GEMINI_API_KEY" in st.secrets:
            return st.secrets["GEMINI_API_KEY"]
    except Exception:  # no hay archivo de secrets
        pass
    return os.environ.get("GEMINI_API_KEY")


API_KEY = _api_key()

with st.sidebar:
    st.header("Modo de lectura")
    modo = st.radio("Modo", list(MODOS), format_func=MODOS.get, label_visibility="collapsed",
                    disabled=not API_KEY, index=0 if API_KEY else 1)
    if not API_KEY:
        st.caption("IA desactivada: falta la API key de Gemini en los Secrets de Streamlit "
                   "(GEMINI_API_KEY).")
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
    st.session_state.uso = None

if st.button("Extraer datos", type="primary", disabled=not archivos):
    client = crear_cliente(API_KEY) if API_KEY and modo != "ocr" else None
    uso = Uso()
    resultados, errores = [], []
    progreso = st.progress(0.0, text="Procesando…")
    for i, archivo in enumerate(archivos):
        progreso.progress(i / len(archivos), text=f"Leyendo {archivo.name}…")
        try:
            resultados.append((archivo.name, procesar(archivo.getvalue(), archivo.type, modo, client, uso)))
        except ErrorExtraccion as e:
            errores.append(f"{archivo.name}: {e}")
        except Exception as e:  # archivo dañado, formato no soportado, etc.
            errores.append(f"{archivo.name}: no se pudo leer ({e})")
    progreso.empty()
    st.session_state.resultados = resultados
    st.session_state.uso = uso
    for err in errores:
        st.error(err)


def _monto(v):
    return f"{v:,.2f}" if v is not None else "—"


resultados = st.session_state.resultados
if resultados:
    con_ia = sum(r.uso_ia for _, r in resultados)
    uso = st.session_state.uso
    resumen = f"{len(resultados)} factura(s) procesada(s)"
    if con_ia:
        resumen += (f" · {con_ia} con IA · costo aprox. US$ {uso.costo_usd:.4f} con key de pago, US$ 0 en el nivel gratuito "
                    f"({uso.tokens_entrada:,} tokens de entrada, {uso.tokens_salida:,} de salida)")
    st.success(resumen + ".")
    st.download_button(
        "⬇️ Descargar Excel",
        data=generar_excel([(n, r.factura) for n, r in resultados]),
        file_name=f"facturas_{datetime.now():%Y%m%d_%H%M}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
    )

    for nombre, r in resultados:
        f = r.factura
        titulo = f"{nombre} — {f.tipo_documento or 'Documento'} {f.numero_factura or ''}"
        if r.uso_ia:
            titulo += " · ✨ IA"
        with st.expander(titulo, expanded=len(resultados) == 1):
            st.caption(f"Lectura: {f.metodo_lectura} · Confianza: {f.confianza}")
            if r.uso_ia and modo == "auto":
                st.caption("Se usó la IA porque en el OCR: " + "; ".join(r.motivos) + ".")
            elif r.motivos and not r.uso_ia:
                st.warning("Revisa esta factura: " + "; ".join(r.motivos) + ".")
            if r.aviso:
                st.info(r.aviso)
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
