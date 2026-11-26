"""Extracción de datos de facturas (foto o PDF) sin APIs de pago.

- PDF digital: se lee el texto y las tablas directamente (pdfplumber), sin errores de OCR.
- PDF escaneado o foto: se aplica OCR con Tesseract (gratuito).
Luego, reglas (expresiones regulares) buscan cada dato en el texto.
"""
from __future__ import annotations

import io
import re
from typing import List, Optional

from PIL import Image, ImageOps
from pydantic import BaseModel

# ---------- Modelo de datos ----------


class Parte(BaseModel):
    nombre_o_razon_social: Optional[str] = None
    identificacion_fiscal: Optional[str] = None
    tipo_identificacion: Optional[str] = None
    direccion: Optional[str] = None
    telefono: Optional[str] = None
    email: Optional[str] = None


class Item(BaseModel):
    codigo: Optional[str] = None
    descripcion: Optional[str] = None
    cantidad: Optional[float] = None
    precio_unitario: Optional[float] = None
    descuento: Optional[float] = None
    total: Optional[float] = None


class Impuesto(BaseModel):
    nombre: Optional[str] = None
    tasa_porcentaje: Optional[float] = None
    monto: Optional[float] = None


class DatoAdicional(BaseModel):
    campo: str
    valor: str


class Factura(BaseModel):
    tipo_documento: Optional[str] = None
    numero_factura: Optional[str] = None
    fecha_emision: Optional[str] = None
    fecha_vencimiento: Optional[str] = None
    moneda: Optional[str] = None
    emisor: Parte = Parte()
    receptor: Parte = Parte()
    items: List[Item] = []
    subtotal: Optional[float] = None
    total_descuentos: Optional[float] = None
    impuestos: List[Impuesto] = []
    total: Optional[float] = None
    forma_de_pago: Optional[str] = None
    numero_autorizacion: Optional[str] = None
    datos_adicionales: List[DatoAdicional] = []
    metodo_lectura: Optional[str] = None
    confianza: Optional[str] = None
    texto_completo: str = ""


class ErrorExtraccion(Exception):
    pass


# ---------- Lectura del archivo -> texto ----------

MIN_TEXTO_PDF = 40  # si un PDF trae menos caracteres, se asume escaneado


def _ocr_imagen(img: Image.Image) -> str:
    import pytesseract

    img = ImageOps.exif_transpose(img).convert("L")
    # Las fotos pequeñas se amplían: Tesseract lee mejor letras de ~30 px de alto.
    if max(img.size) < 2000:
        escala = 2000 / max(img.size)
        img = img.resize((int(img.width * escala), int(img.height * escala)), Image.LANCZOS)
    img = ImageOps.autocontrast(img)
    try:
        return pytesseract.image_to_string(img, lang="spa+eng", config="--psm 6")
    except pytesseract.TesseractNotFoundError:
        raise ErrorExtraccion(
            "Tesseract no está instalado. En Streamlit Cloud se instala con packages.txt; "
            "en Windows descárgalo de github.com/UB-Mannheim/tesseract."
        )


def _leer_pdf(datos: bytes) -> tuple[str, list[list[list]], str]:
    import pdfplumber

    textos, tablas = [], []
    with pdfplumber.open(io.BytesIO(datos)) as pdf:
        for pagina in pdf.pages:
            textos.append(pagina.extract_text() or "")
            tablas += pagina.extract_tables() or []
    texto = "\n".join(textos)
    if len(texto.strip()) >= MIN_TEXTO_PDF:
        return texto, tablas, "PDF digital"

    # PDF escaneado: convertir cada página en imagen y aplicar OCR.
    from pdf2image import convert_from_bytes

    paginas = convert_from_bytes(datos, dpi=300)
    return "\n".join(_ocr_imagen(p) for p in paginas), [], "OCR (PDF escaneado)"


def leer_texto(datos: bytes, tipo_mime: str) -> tuple[str, list, str]:
    if tipo_mime == "application/pdf":
        return _leer_pdf(datos)
    return _ocr_imagen(Image.open(io.BytesIO(datos))), [], "OCR (imagen)"


# ---------- Utilidades de reglas ----------

NUM = r"-?\d{1,3}(?:[.,\s]\d{3})*(?:[.,]\d{1,4})?|-?\d+(?:[.,]\d{1,4})?"


def a_numero(texto: str | None) -> Optional[float]:
    """Convierte '1.234,56', '1,234.56', '1234.56' o '1 234,56' en float."""
    if not texto:
        return None
    t = re.sub(r"[^\d,.\-]", "", texto.replace(" ", ""))
    if not t or t in "-.,":
        return None
    if "," in t and "." in t:
        # el separador que aparece al final es el decimal
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t:
        partes = t.split(",")
        t = t.replace(",", ".") if len(partes[-1]) in (1, 2) else t.replace(",", "")
    elif t.count(".") > 1:
        t = t.replace(".", "")
    elif "." in t and len(t.split(".")[-1]) == 3 and len(t.split(".")[0]) <= 3:
        t = t.replace(".", "")  # '1.234' como miles
    try:
        return float(t)
    except ValueError:
        return None


def _ultimo_numero(linea: str) -> Optional[float]:
    numeros = re.findall(NUM, linea)
    return a_numero(numeros[-1]) if numeros else None


def _lineas(texto: str) -> list[str]:
    return [l.strip() for l in texto.splitlines() if l.strip()]


def _buscar(patron: str, texto: str, flags=re.I) -> Optional[str]:
    m = re.search(patron, texto, flags)
    return m.group(1).strip() if m else None


MESES = {m: i for i, m in enumerate(
    ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
     "septiembre", "octubre", "noviembre", "diciembre"], 1)}
MESES["setiembre"] = 9

FECHA = (r"(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}|\d{4}[/\-.]\d{1,2}[/\-.]\d{1,2}"
         r"|\d{1,2}\s+de\s+[a-zA-Záé]+\s+(?:de|del)\s+\d{4})")


def normalizar_fecha(f: str | None) -> Optional[str]:
    if not f:
        return None
    m = re.match(r"(\d{1,2})\s+de\s+([a-záé]+)\s+(?:de|del)\s+(\d{4})", f, re.I)
    if m and m.group(2).lower() in MESES:
        return f"{m.group(3)}-{MESES[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    partes = re.split(r"[/\-.]", f)
    if len(partes) != 3:
        return f
    if len(partes[0]) == 4:
        a, me, d = partes
    else:
        d, me, a = partes
        if len(a) == 2:
            a = "20" + a
    try:
        return f"{int(a):04d}-{int(me):02d}-{int(d):02d}"
    except ValueError:
        return f


def _fecha_cerca(texto: str, etiqueta: str) -> Optional[str]:
    return _buscar(rf"(?:{etiqueta})[^\n\d]{{0,40}}{FECHA}", texto)


# ---------- Identificaciones fiscales ----------

IDENTIFICACIONES = [
    ("RUC", r"R\.?\s?U\.?\s?C\.?\s*[:#Nº°o.]*\s*(\d{11,13})"),
    ("NIT", r"N\.?\s?I\.?\s?T\.?\s*[:#Nº°o.]*\s*([\d.\s]{6,15}-?\s?\d?)"),
    ("RFC", r"\b([A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3})\b"),
    ("CUIT", r"\b(\d{2}-\d{8}-\d)\b"),
    ("RIF", r"\b([JVEGP]-?\d{8}-?\d)\b"),
    ("RUT", r"R\.?\s?U\.?\s?T\.?\s*[:#Nº°o.]*\s*(\d{1,2}\.?\d{3}\.?\d{3}-[\dkK])"),
    ("Cédula/DNI", r"(?:C[ÉE]DULA|C\.?\s?I\.?|DNI|C\.?\s?C\.?)\s*[:#Nº°o.]*\s*(\d{7,10})"),
]


def _identificaciones(texto: str) -> list[tuple[str, str, int]]:
    """Devuelve (tipo, valor, posición) en el orden en que aparecen."""
    halladas = []
    for tipo, patron in IDENTIFICACIONES:
        flags = 0 if tipo in ("RFC", "RIF") else re.I
        for m in re.finditer(patron, texto, flags):
            valor = re.sub(r"\s", "", m.group(1))
            if all(valor != v for _, v, _ in halladas):
                halladas.append((tipo, valor, m.start()))
    return sorted(halladas, key=lambda x: x[2])


# ---------- Ítems ----------

CABECERAS = {
    "cantidad": ("cant", "qty", "unid"),
    "descripcion": ("descrip", "detalle", "concepto", "producto", "servicio", "artículo", "articulo"),
    "precio_unitario": ("unit", "p.u", "p. u", "precio"),
    "descuento": ("desc.", "dscto", "dcto", "descuento"),
    "total": ("total", "importe", "valor", "subtotal", "monto"),
    "codigo": ("cód", "cod", "código", "codigo", "ref", "sku"),
}


def _items_de_tablas(tablas: list) -> list[Item]:
    for tabla in tablas:
        if not tabla or len(tabla) < 2:
            continue
        cab = [(c or "").lower() for c in tabla[0]]
        mapa = {}
        for campo, claves in CABECERAS.items():
            for i, c in enumerate(cab):
                if i not in mapa.values() and any(k in c for k in claves):
                    # "precio" sin "unit" puede ser total; la última columna coincidente es el total
                    mapa.setdefault(campo, i)
                    break
        if "descripcion" not in mapa or len(mapa) < 3:
            continue
        items = []
        for fila in tabla[1:]:
            fila = [(c or "").strip() for c in fila]
            desc = fila[mapa["descripcion"]] if mapa["descripcion"] < len(fila) else ""
            if not desc or re.match(r"(sub)?total", desc, re.I):
                continue
            val = lambda k: fila[mapa[k]] if k in mapa and mapa[k] < len(fila) else None
            items.append(Item(
                codigo=val("codigo") or None,
                descripcion=desc.replace("\n", " "),
                cantidad=a_numero(val("cantidad")),
                precio_unitario=a_numero(val("precio_unitario")),
                descuento=a_numero(val("descuento")),
                total=a_numero(val("total")),
            ))
        if items:
            return items
    return []


PALABRAS_RESUMEN = re.compile(r"(sub\s?total|total|iva|igv|impuesto|descuento|ret|propina|saldo)", re.I)


def _items_de_texto(lineas: list[str]) -> list[Item]:
    """Líneas tipo '2 Servicio 50,00 100,00' o 'A01 2 Laptop 650,00 0,00 1.300,00'
    (código opcional, cantidad, descripción, precio unitario, descuento opcional, total)."""
    n = r"-?\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,4})?|-?\d+(?:[.,]\d{1,4})?"
    patron = re.compile(
        rf"^(?:([A-Za-z0-9][\w\-./]{{0,15}})\s+)?(\d+(?:[.,]\d+)?)\s+(.{{3,}}?)\s+({n})(?:\s+({n}))?\s+({n})$")
    items = []
    for l in lineas:
        if PALABRAS_RESUMEN.search(l):
            continue
        m = patron.match(l)
        if m and re.search(r"[A-Za-zÁÉÍÓÚáéíóúñÑ]{3,}", m.group(3)):
            items.append(Item(
                codigo=m.group(1),
                cantidad=a_numero(m.group(2)),
                descripcion=m.group(3).strip(),
                precio_unitario=a_numero(m.group(4)),
                descuento=a_numero(m.group(5)),
                total=a_numero(m.group(6)),
            ))
    return items


# ---------- Extracción principal ----------

def _monto_etiqueta(lineas: list[str], patron: str, excluir: str | None = None) -> Optional[float]:
    for l in lineas:
        if re.search(patron, l, re.I) and not (excluir and re.search(excluir, l, re.I)):
            v = _ultimo_numero(l)
            if v is not None:
                return v
    return None


def _nombre_emisor(lineas: list[str]) -> Optional[str]:
    for l in lineas[:8]:
        if re.search(r"[A-Za-zÁÉÍÓÚÑ]{3,}", l) and not re.search(
                r"factura|r\.?u\.?c|nit|rfc|fecha|tel|direcci|e-?mail|@|www|autoriz", l, re.I):
            return l
    return None


def extraer_de_texto(texto: str, tablas: list | None = None, metodo: str = "") -> Factura:
    lineas = _lineas(texto)
    f = Factura(texto_completo=texto, metodo_lectura=metodo)

    # Tipo y número
    f.tipo_documento = _buscar(
        r"\b(factura(?:\s+electr[oó]nica)?|nota\s+de\s+cr[eé]dito|nota\s+de\s+d[eé]bito|boleta(?:\s+de\s+venta)?"
        r"|recibo|comprobante(?:\s+de\s+venta)?|ticket)\b", texto)
    if f.tipo_documento:
        f.tipo_documento = f.tipo_documento.title()
    f.numero_factura = (
        _buscar(r"\b(\d{3}-\d{3}-\d{6,9})\b", texto)                       # Ecuador 001-001-000000123
        or _buscar(r"\b([FBE][A-Z0-9]{3}\s?-\s?\d{1,8})\b", texto, 0)       # Perú F001-123
        or _buscar(r"(?:factura|comprobante|boleta|documento|folio)[^\n]{0,15}?"
                   r"(?:n[°ºo.]*|no\.?|nro\.?|núm(?:ero)?\.?|#)\s*[:.]?\s*([A-Z0-9][A-Z0-9\-]{2,})", texto)
        or _buscar(r"\b(?:n[°º]|nro\.?|no\.)\s*[:.]?\s*([A-Z0-9][A-Z0-9\-]{2,})", texto)
    )

    # Fechas
    f.fecha_emision = normalizar_fecha(
        _fecha_cerca(texto, r"emisi[oó]n|fecha\s+de\s+factura|fecha(?!\s+de\s+venc)") or _buscar(FECHA, texto))
    f.fecha_vencimiento = normalizar_fecha(_fecha_cerca(texto, r"venc"))

    # Moneda
    for patron, moneda in [(r"\bUSD\b|US\$|d[oó]lar", "USD"), (r"\bPEN\b|S/\.?|soles", "PEN"),
                           (r"\bCOP\b", "COP"), (r"\bMXN\b", "MXN"), (r"\bEUR\b|€", "EUR"),
                           (r"\bCLP\b", "CLP"), (r"\bARS\b", "ARS"), (r"\bBs\.?\b|\bVES\b", "VES"),
                           (r"\$", "$")]:
        if re.search(patron, texto, re.I if moneda != "$" else 0):
            f.moneda = moneda
            break

    # Identificaciones: la primera suele ser la del emisor, la segunda la del cliente.
    ids = _identificaciones(texto)
    if ids:
        f.emisor.tipo_identificacion, f.emisor.identificacion_fiscal, _ = ids[0]
    if len(ids) > 1:
        f.receptor.tipo_identificacion, f.receptor.identificacion_fiscal, _ = ids[1]
    for tipo, valor, _ in ids[2:]:
        f.datos_adicionales.append(DatoAdicional(campo=tipo, valor=valor))

    f.emisor.nombre_o_razon_social = _nombre_emisor(lineas)
    f.receptor.nombre_o_razon_social = _buscar(
        r"(?:cliente|se[ñn]or(?:\(?es\)?)?|sr\.?\(?es\)?|raz[oó]n\s+social(?:\s*/\s*nombres?)?|adquiriente|comprador"
        r"|facturar\s+a|nombre)\s*[:.]\s*([^\n]{3,80}?)(?:\s{2,}|\s+(?:R\.?U\.?C|NIT|RFC|C[ÉE]DULA|DNI|ID)\b|$)",
        texto, re.I | re.M)

    # Contacto
    emails = re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", texto)
    telefonos = re.findall(r"(?:tel[ée]?f?o?n?o?|cel(?:ular)?|m[oó]vil|tlf)\.?\s*[:.]?\s*([+\d][\d\s\-()]{6,18}\d)",
                           texto, re.I)
    direcciones = re.findall(r"(?:direcci[oó]n|dir\.|domicilio)\s*(?:matriz|sucursal|fiscal)?\s*[:.]\s*([^\n]{5,100})",
                             texto, re.I)
    if emails:
        f.emisor.email = emails[0]
    if len(emails) > 1:
        f.receptor.email = emails[1]
    if telefonos:
        f.emisor.telefono = telefonos[0].strip()
    if len(telefonos) > 1:
        f.receptor.telefono = telefonos[1].strip()
    if direcciones:
        f.emisor.direccion = direcciones[0].strip()
    if len(direcciones) > 1:
        f.receptor.direccion = direcciones[1].strip()

    # Montos
    f.subtotal = (_monto_etiqueta(lineas, r"sub\s?-?\s?total", excluir=r"\b0\s*%|exent|no\s+objeto")
                  or _monto_etiqueta(lineas, r"op\.?\s*gravada|valor\s+(?:de\s+)?venta|base\s+imponible"))
    f.total_descuentos = _monto_etiqueta(lineas, r"descuento|dscto", excluir=r"^\d")
    total_lineas = [l for l in lineas if re.search(r"\btotal\b", l, re.I)
                    and not re.search(r"sub\s?-?\s?total|total\s+(?:iva|igv|impuesto|descuento|items|ítems)", l, re.I)]
    preferidas = [l for l in total_lineas if re.search(r"pagar|importe\s+total|valor\s+total|total\s+general|^total\b", l, re.I)]
    for l in preferidas + total_lineas:
        v = _ultimo_numero(l)
        if v is not None:
            f.total = v
            break

    for l in lineas:
        m = re.search(r"\b(IVA|IGV|ISC|ICE|IEPS|ITBIS|ISV|IVU|impuesto|retenci[oó]n)\b", l, re.I)
        if not m or re.search(r"sub\s?total.*\b0\s*%|no\s+objeto|exento", l, re.I):
            continue
        if re.search(r"incluye|inclu|régimen|regimen|agente", l, re.I):
            continue
        monto = _ultimo_numero(l)
        tasa = _buscar(r"(\d{1,2}(?:[.,]\d+)?)\s*%", l)
        if monto is not None and (tasa is None or a_numero(tasa) != monto):
            f.impuestos.append(Impuesto(nombre=m.group(1).upper(), tasa_porcentaje=a_numero(tasa), monto=monto))

    # Pago y autorización
    f.forma_de_pago = _buscar(r"(?:forma|m[eé]todo|medio|condici[oó]n)\s+de\s+pago\s*[:.]?\s*([^\n]{3,60})", texto)
    f.numero_autorizacion = _buscar(
        r"(?:autorizaci[oó]n|clave\s+de\s+acceso|CUFE|CAE|folio\s+fiscal|UUID)[^\n\w]{0,20}([A-F0-9\-]{10,})",
        texto, re.I)

    # Ítems
    f.items = _items_de_tablas(tablas or []) or _items_de_texto(lineas)

    # Confianza según cuántos datos clave se encontraron
    clave = [f.numero_factura, f.fecha_emision, f.total, f.emisor.identificacion_fiscal, f.emisor.nombre_o_razon_social]
    hallados = sum(v is not None for v in clave)
    f.confianza = "alta" if hallados >= 5 else "media" if hallados >= 3 else "baja"
    return f


def extraer_factura(datos: bytes, tipo_mime: str) -> Factura:
    texto, tablas, metodo = leer_texto(datos, tipo_mime)
    if not texto.strip():
        raise ErrorExtraccion("No se encontró texto legible en el documento.")
    return extraer_de_texto(texto, tablas, metodo)
