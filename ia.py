"""Lectura de facturas con IA (Claude Haiku 5.5) y modo híbrido OCR + IA.

Modo híbrido: primero se lee gratis con OCR + reglas; solo si los datos no cuadran
(faltan campos clave o las sumas no coinciden) se envía la factura a la IA.
"""
from __future__ import annotations

import base64
import io
from dataclasses import dataclass, field
from typing import List, Optional

import anthropic
from PIL import Image, ImageOps
from pydantic import BaseModel

from extractor import DatoAdicional, ErrorExtraccion, Factura, Impuesto, Item, Parte, extraer_factura

MODELO_IA = "claude-haiku-5-5"
PRECIO_ENTRADA = 0.10 / 1_000_000   # US$ por token (prompts de hasta 100K tokens)
PRECIO_SALIDA = 0.50 / 1_000_000
LADO_MAXIMO_IA = 1600  # px: suficiente para leer una factura y mantiene bajo el costo

MODOS = {
    "auto": "Automático: OCR gratis y la IA solo si algo no cuadra",
    "ocr": "Solo OCR (gratis)",
    "ia": "Siempre con IA",
}


# ---------- Esquema que devuelve la IA ----------

class FacturaIA(BaseModel):
    tipo_documento: Optional[str]
    numero_factura: Optional[str]
    fecha_emision: Optional[str]
    fecha_vencimiento: Optional[str]
    moneda: Optional[str]
    emisor: Parte
    receptor: Parte
    items: List[Item]
    subtotal: Optional[float]
    total_descuentos: Optional[float]
    impuestos: List[Impuesto]
    total: Optional[float]
    total_en_letras: Optional[str]
    forma_de_pago: Optional[str]
    numero_autorizacion: Optional[str]
    datos_adicionales: List[DatoAdicional]


INSTRUCCIONES = """Lee esta factura o comprobante y extrae sus datos.
- Copia textos y números exactamente como aparecen (RUC, nombres, números de serie).
- Importes como números con punto decimal, sin símbolo de moneda ni separador de miles.
- Fechas en formato AAAA-MM-DD. Moneda en código ISO (PEN, USD, EUR...).
- emisor = quien vende/emite; receptor = el cliente. nombre_o_razon_social es la razón social legal
  (la que termina en SAC, SRL, EIRL, S.A., etc.); nombre_comercial es el nombre de fantasía si lo hay.
- Una entrada en items por cada línea de producto o servicio.
- Cualquier otro dato visible sin campo propio (cajero, mesa, hora, placa, guía...) va en datos_adicionales.
- Si un dato no aparece, usa null. No inventes nada."""


# ---------- Validación de los datos leídos por OCR ----------

def _cerca(a: float, b: float) -> bool:
    return abs(a - b) <= max(0.05, abs(b) * 0.01)


def problemas(f: Factura) -> list[str]:
    """Lista de motivos por los que la lectura OCR no es confiable (vacía = todo cuadra)."""
    p = []
    if not f.numero_factura:
        p.append("no se encontró el número")
    if not f.fecha_emision:
        p.append("no se encontró la fecha")
    if not f.emisor.identificacion_fiscal:
        p.append("no se encontró el RUC/NIT del emisor")
    if f.total is None:
        p.append("no se encontró el total")
    if not f.items:
        p.append("no se encontraron ítems")
    else:
        if any(i.cantidad and i.precio_unitario is not None and i.total is not None
               and not _cerca(i.cantidad * i.precio_unitario - (i.descuento or 0), i.total) for i in f.items):
            p.append("algún ítem no cuadra (cantidad × precio ≠ total)")
        suma = sum(i.total or 0 for i in f.items)
        if f.total is not None and not any(
                _cerca(suma, ref) for ref in (f.total, f.subtotal) if ref is not None):
            p.append("la suma de ítems no coincide con el subtotal ni el total")
    if f.subtotal is not None and f.total is not None and f.impuestos:
        esperado = f.subtotal + sum(i.monto or 0 for i in f.impuestos) - (f.total_descuentos or 0)
        if not _cerca(esperado, f.total) and not _cerca(f.subtotal, f.total):
            p.append("subtotal + impuestos ≠ total")
    return p


# ---------- Llamada a la IA ----------

def _bloque(datos: bytes, tipo_mime: str) -> dict:
    if tipo_mime == "application/pdf":
        return {"type": "document", "source": {
            "type": "base64", "media_type": "application/pdf",
            "data": base64.standard_b64encode(datos).decode()}}
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(datos)))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail((LADO_MAXIMO_IA, LADO_MAXIMO_IA))
    salida = io.BytesIO()
    img.save(salida, format="JPEG", quality=88)
    return {"type": "image", "source": {
        "type": "base64", "media_type": "image/jpeg",
        "data": base64.standard_b64encode(salida.getvalue()).decode()}}


@dataclass
class Uso:
    tokens_entrada: int = 0
    tokens_salida: int = 0

    @property
    def costo_usd(self) -> float:
        return self.tokens_entrada * PRECIO_ENTRADA + self.tokens_salida * PRECIO_SALIDA


def extraer_con_ia(client: anthropic.Anthropic, datos: bytes, tipo_mime: str, uso: Uso) -> FacturaIA:
    respuesta = client.messages.parse(
        model=MODELO_IA,
        max_tokens=8000,
        output_config={"effort": "low"},  # extracción simple: poco razonamiento, menos tokens
        messages=[{"role": "user", "content": [
            _bloque(datos, tipo_mime),
            {"type": "text", "text": INSTRUCCIONES},
        ]}],
        output_format=FacturaIA,
    )
    uso.tokens_entrada += respuesta.usage.input_tokens
    uso.tokens_salida += respuesta.usage.output_tokens

    if respuesta.stop_reason == "refusal":
        raise ErrorExtraccion("la IA no pudo procesar este documento")
    if respuesta.stop_reason == "max_tokens":
        raise ErrorExtraccion("la factura es demasiado larga para la IA; la respuesta quedó incompleta")
    if respuesta.parsed_output is None:
        raise ErrorExtraccion("no se pudo interpretar la respuesta de la IA")
    return respuesta.parsed_output


# ---------- Proceso completo ----------

@dataclass
class Resultado:
    factura: Factura
    uso_ia: bool = False
    motivos: list[str] = field(default_factory=list)  # por qué se usó la IA o qué no cuadra
    aviso: Optional[str] = None


def procesar(datos: bytes, tipo_mime: str, modo: str,
             client: Optional[anthropic.Anthropic], uso: Uso) -> Resultado:
    ocr: Optional[Factura] = None
    error_ocr: Optional[Exception] = None
    try:
        ocr = extraer_factura(datos, tipo_mime)
    except ErrorExtraccion as e:
        error_ocr = e

    motivos = problemas(ocr) if ocr else [str(error_ocr)]
    usar_ia = client is not None and (modo == "ia" or (modo == "auto" and motivos))

    if not usar_ia:
        if ocr is None:
            raise error_ocr
        aviso = None
        if motivos and modo != "ocr" and client is None:
            aviso = "Configura la API key para que la IA revise las facturas que no cuadran."
        return Resultado(ocr, motivos=motivos, aviso=aviso)

    try:
        ia = extraer_con_ia(client, datos, tipo_mime, uso)
    except (ErrorExtraccion, anthropic.APIError) as e:
        if ocr is None:
            raise ErrorExtraccion(f"no se pudo leer con OCR ni con IA ({e})")
        return Resultado(ocr, motivos=motivos, aviso=f"La IA falló ({e}); se muestran los datos del OCR.")

    factura = Factura(**ia.model_dump(),
                      texto_completo=ocr.texto_completo if ocr else "",
                      metodo_lectura=f"IA ({MODELO_IA})")
    pendientes = problemas(factura)
    factura.confianza = "alta" if not pendientes else "media"
    return Resultado(factura, uso_ia=True,
                     motivos=motivos if modo == "auto" else ["modo Siempre con IA"])
