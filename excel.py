"""Convierte las facturas extraídas en un archivo Excel."""
from __future__ import annotations

import io

import pandas as pd

from extractor import Factura


def _aplanar_parte(prefijo: str, parte) -> dict:
    return {f"{prefijo}_{k}": v for k, v in parte.model_dump().items()}


def generar_excel(facturas: list[tuple[str, Factura]]) -> bytes:
    """facturas: lista de (nombre_archivo, Factura). Devuelve el .xlsx en bytes."""
    filas, items, impuestos, adicionales, textos = [], [], [], [], []

    for archivo, f in facturas:
        clave = {"archivo": archivo, "numero_factura": f.numero_factura}
        datos = f.model_dump(exclude={"emisor", "receptor", "items", "impuestos", "datos_adicionales",
                                      "texto_completo"})
        textos += [{"archivo": archivo, "linea": i, "texto": l}
                   for i, l in enumerate(f.texto_completo.splitlines(), 1) if l.strip()]
        filas.append({
            "archivo": archivo,
            **datos,
            **_aplanar_parte("emisor", f.emisor),
            **_aplanar_parte("receptor", f.receptor),
            "cantidad_items": len(f.items),
        })
        items += [{**clave, "linea": i, **it.model_dump()} for i, it in enumerate(f.items, 1)]
        impuestos += [{**clave, **imp.model_dump()} for imp in f.impuestos]
        adicionales += [{**clave, **d.model_dump()} for d in f.datos_adicionales]

    hojas = {
        "Facturas": pd.DataFrame(filas),
        "Items": pd.DataFrame(items),
        "Impuestos": pd.DataFrame(impuestos),
        "Otros datos": pd.DataFrame(adicionales),
        "Texto completo": pd.DataFrame(textos),
    }

    salida = io.BytesIO()
    with pd.ExcelWriter(salida, engine="openpyxl") as writer:
        for nombre, df in hojas.items():
            if df.empty:
                df = pd.DataFrame({"sin datos": []})
            df.to_excel(writer, sheet_name=nombre, index=False)
            hoja = writer.sheets[nombre]
            hoja.freeze_panes = "A2"
            for col in hoja.columns:
                ancho = max(len(str(c.value or "")) for c in col)
                hoja.column_dimensions[col[0].column_letter].width = min(max(ancho + 2, 10), 80)
    return salida.getvalue()
