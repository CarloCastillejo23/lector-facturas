"""Lectura de facturas con IA (Google Gemini) y modo híbrido OCR + IA.

Modo híbrido: primero se lee gratis con OCR + reglas; solo si los datos no cuadran
(faltan campos clave o las sumas no coinciden) se envía la factura a la IA.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import List, Optional

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from PIL import Image, ImageOps
from pydantic import BaseModel, ValidationError

from extractor import DatoAdicional, ErrorExtraccion, Factura, Impuesto, Item, Parte, extraer_factura

MODELO_IA = "gemini-3.5-flash-lite"
# Precios del nivel de pago (US$ por token). En el nivel gratuito de Google AI Studio el costo es 0.
PRECIO_ENTRADA = 0.30 / 1_000_000
PRECIO_SALIDA = 2.50 / 1_000_000   # incluye los tokens de razonamiento
LADO_MAXIMO_IA = 1600  # px: suficiente para leer una factura y mantiene bajo el consumo

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

def _parte_documento(datos: bytes, tipo_mime: str) -> types.Part:
    if tipo_mime == "application/pdf":
        return types.Part.from_bytes(data=datos, mime_type="application/pdf")
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(datos)))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail((LADO_MAXIMO_IA, LADO_MAXIMO_IA))
    salida = io.BytesIO()
    img.save(salida, format="JPEG", quality=88)
    return types.Part.from_bytes(data=salida.getvalue(), mime_type="image/jpeg")


@dataclass
class Uso:
    tokens_entrada: int = 0
    tokens_salida: int = 0

    @property
    def costo_usd(self) -> float:
        """Costo si la key es del nivel de pago (en el nivel gratuito es 0)."""
        return self.tokens_entrada * PRECIO_ENTRADA + self.tokens_salida * PRECIO_SALIDA


def crear_cliente(api_key: str) -> genai.Client:
    return genai.Client(api_key=api_key)


def extraer_con_ia(client: genai.Client, datos: bytes, tipo_mime: str, uso: Uso) -> FacturaIA:
    respuesta = client.models.generate_content(
        model=MODELO_IA,
        contents=[_parte_documento(datos, tipo_mime), INSTRUCCIONES],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=FacturaIA.model_json_schema(),
            max_output_tokens=8000,
        ),
    )
    meta = respuesta.usage_metadata
    if meta:
        uso.tokens_entrada += meta.prompt_token_count or 0
        uso.tokens_salida += (meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0)

    candidato = respuesta.candidates[0] if respuesta.candidates else None
    motivo_fin = str(candidato.finish_reason) if candidato and candidato.finish_reason else ""
    if "MAX_TOKENS" in motivo_fin:
        raise ErrorExtraccion("la factura es demasiado larga para la IA; la respuesta quedó incompleta")
    if not respuesta.text:
        raise ErrorExtraccion(f"la IA no devolvió datos ({motivo_fin or 'respuesta bloqueada'})")
    try:
        return FacturaIA.model_validate_json(respuesta.text)
    except ValidationError:
        raise ErrorExtraccion("no se pudo interpretar la respuesta de la IA")


# ---------- Proceso completo ----------

@dataclass
class Resultado:
    factura: Factura
    uso_ia: bool = False
    motivos: list[str] = field(default_factory=list)  # por qué se usó la IA o qué no cuadra
    aviso: Optional[str] = None


def procesar(datos: bytes, tipo_mime: str, modo: str,
             client: Optional[genai.Client], uso: Uso) -> Resultado:
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
            aviso = "Configura la API key de Gemini para que la IA revise las facturas que no cuadran."
        return Resultado(ocr, motivos=motivos, aviso=aviso)

    try:
        ia = extraer_con_ia(client, datos, tipo_mime, uso)
    except (ErrorExtraccion, genai_errors.APIError) as e:
        if ocr is None:
            raise ErrorExtraccion(f"no se pudo leer con OCR ni con IA ({e})")
        return Resultado(ocr, motivos=motivos, aviso=f"La IA falló ({e}); se muestran los datos del OCR.")

    factura = Factura(**ia.model_dump(),
                      texto_completo=ocr.texto_completo if ocr else "",
                      metodo_lectura=f"IA ({MODELO_IA})")
    factura.confianza = "alta" if not problemas(factura) else "media"
    return Resultado(factura, uso_ia=True,
                     motivos=motivos if modo == "auto" else ["modo Siempre con IA"])
