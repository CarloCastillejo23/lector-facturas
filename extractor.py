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
    nombre_comercial: Optional[str] = None
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
    total_en_letras: Optional[str] = None
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

# Número con miles agrupados (1.234,56 / 1,234.56 / 1 234,56) o simple (1234.56, 10700); nunca parte un número.
NUM = r"(?<![\d.,])-?(?:\d{1,3}(?:[.,\s]\d{3})+(?:[.,]\d{1,4})?|\d+(?:[.,]\d{1,4})?)(?!\d)"


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


PALABRAS_RESUMEN = re.compile(
    r"\b(sub\s?total|total|iva|igv|impuesto|descuento|retenci[oó]n|propina|saldo|op\.?\s*gravad\w*|icbper)\b", re.I)


TOKEN_NUM = re.compile(r"^-?\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,4})?$|^-?\d+(?:[.,]\d{1,4})?$")
CODIGO = re.compile(r"^(?=.*\d)[A-Za-z0-9][\w\-./]{0,15}$")


def _variantes(token: str) -> list[float]:
    """Valor leído y, si el OCR pudo comerse el punto decimal ('4900'), el valor /100."""
    v = a_numero(token)
    if v is None:
        return []
    if re.fullmatch(r"\d{3,}", token):
        return [v, v / 100]
    return [v]


def _cuadra(cant: float, pu: float, desc: float, total: float) -> bool:
    return cant > 0 and abs(cant * pu - desc - total) <= max(0.02, total * 0.01)


def _item_de_linea(linea: str, cant_al_final: bool | None) -> Optional[Item]:
    tokens = linea.split()
    nums = []
    while tokens and TOKEN_NUM.match(tokens[-1]) and len(nums) < 4:
        nums.insert(0, tokens.pop())
    if len(nums) < 2 or not tokens:
        return None

    candidatos = []  # (cuadra, prioridad, Item)
    # A) [código] descripción CANT PU [DESC] TOTAL   (cantidad después de la descripción)
    if len(nums) >= 3:
        codigo = tokens[0] if len(tokens) > 1 and CODIGO.match(tokens[0]) else None
        desc_txt = " ".join(tokens[1:] if codigo else tokens)
        c_tok, pu_tok, tot_tok = nums[0], nums[1], nums[-1]
        dsc = a_numero(nums[2]) if len(nums) == 4 else 0.0
        for pu in _variantes(pu_tok):
            for tot in _variantes(tot_tok):
                cant = a_numero(c_tok)
                ok = _cuadra(cant or 0, pu, dsc or 0, tot)
                candidatos.append((ok, cant_al_final is not False, Item(
                    codigo=codigo, descripcion=desc_txt, cantidad=cant, precio_unitario=pu,
                    descuento=dsc or None, total=tot)))
    # B) [código] CANT descripción PU [DESC] TOTAL   (cantidad antes de la descripción)
    for i in (0, 1):
        if len(tokens) > i + 1 and TOKEN_NUM.match(tokens[i]) and len(nums) in (2, 3):
            codigo = tokens[0] if i == 1 and CODIGO.match(tokens[0]) else None
            if i == 1 and not codigo:
                continue
            cant = a_numero(tokens[i])
            desc_txt = " ".join(tokens[i + 1:])
            dsc = a_numero(nums[1]) if len(nums) == 3 else 0.0
            for pu in _variantes(nums[0]):
                for tot in _variantes(nums[-1]):
                    ok = _cuadra(cant or 0, pu, dsc or 0, tot)
                    candidatos.append((ok, cant_al_final is not True, Item(
                        codigo=codigo, descripcion=desc_txt, cantidad=cant, precio_unitario=pu,
                        descuento=dsc or None, total=tot)))

    candidatos = [c for c in candidatos if re.search(r"[A-Za-zÁÉÍÓÚáéíóúñÑ]{3,}", c[2].descripcion or "")]
    if not candidatos:
        return None
    # Primero los que cuadran (cant × pu − desc = total); luego el orden que indica la cabecera.
    candidatos.sort(key=lambda c: (not c[0], not c[1]))
    return candidatos[0][2]


def _items_de_texto(lineas: list[str]) -> list[Item]:
    """Lee ítems línea por línea, aceptando la cantidad antes o después de la descripción."""
    # Si hay cabecera (COD PRODUCTO CANT PU TOTAL) se usa para saber dónde va la cantidad
    # y solo se leen las líneas que siguen a ella.
    inicio, cant_al_final = 0, None
    for i, l in enumerate(lineas):
        bajo = l.lower()
        p_desc = re.search(r"descrip|detalle|concepto|producto|art[ií]culo", bajo)
        p_cant = re.search(r"\bcant|\bqty|\bund\b|\bunid", bajo)
        if p_desc and p_cant:
            inicio, cant_al_final = i + 1, p_cant.start() > p_desc.start()
            break
    items = []
    for l in lineas[inicio:]:
        if PALABRAS_RESUMEN.search(l):
            if items:
                break  # terminó la tabla de ítems
            continue
        item = _item_de_linea(l, cant_al_final)
        if item:
            items.append(item)
    return items


# ---------- Extracción principal ----------

def _monto_etiqueta(lineas: list[str], patron: str, excluir: str | None = None) -> Optional[float]:
    for l in lineas:
        if re.search(patron, l, re.I) and not (excluir and re.search(excluir, l, re.I)):
            v = _ultimo_numero(l)
            if v is not None:
                return v
    return None


SOCIEDAD = re.compile(
    r"\b(S\.?\s?A\.?\s?C\.?|S\.?\s?R\.?\s?L\.?|E\.?\s?I\.?\s?R\.?\s?L\.?|S\.?\s?A\.?\s?A\.?|S\.?\s?A\.?\s?S\.?"
    r"|S\.?\s?A\.?|S\.?\s?C\.?\s?R\.?\s?L\.?|LTDA\.?|C\.?\s?A\.?|CIA\.?|S\.?\s?de\s?R\.?\s?L\.?|S\.?\s?L\.?)(?=\W|$)",
    re.I)
DIRECCION = re.compile(r"^(?:av\.?|avenida|jr\.?|jir[oó]n|calle|ca\.|cl\.?|carrera|cra\.?|mz\.?|manzana|psje\.?"
                       r"|pasaje|carretera|urb\.?|prolongaci[oó]n|prol\.?|km\.?|lote|lt\.?)\s", re.I)
NO_ES_NOMBRE = re.compile(r"factura|boleta|r\.?u\.?c|\bnit\b|\brfc\b|fecha|tel[eé]f|direcci|e-?mail|@|www|autoriz"
                          r"|cliente|se[ñn]or|\d{6,}", re.I)


def _nombres_emisor(lineas: list[str]) -> tuple[Optional[str], Optional[str]]:
    """(nombre comercial, razón social) a partir de las primeras líneas del documento."""
    cabecera = [l for l in lineas[:8] if re.search(r"[A-Za-zÁÉÍÓÚÑ]{3,}", l)
                and not NO_ES_NOMBRE.search(l) and not DIRECCION.match(l)]
    razon = next((l for l in cabecera if SOCIEDAD.search(l)), None)
    comercial = next((l for l in cabecera if l != razon), None)
    if razon is None:
        return None, comercial
    # Si el nombre comercial aparece después de la razón social, probablemente es otra cosa (eslogan, rubro).
    if comercial and lineas.index(comercial) > lineas.index(razon):
        comercial = None
    return comercial, razon


def _datos_etiquetados(lineas: list[str]) -> list[DatoAdicional]:
    """Líneas 'ETIQUETA: valor' que no tienen columna propia (cajero, mesa, vendedor, hora…)."""
    ya_usadas = re.compile(r"r\.?u\.?c|\bnit\b|\brfc\b|fecha|cliente|se[ñn]or|direc|tel[eé]f|e-?mail|total|igv|iva"
                           r"|gravad|descuento|factura|autoriz|forma\s+de\s+pago|^son\b|moneda|raz[oó]n|nombre", re.I)
    datos = []
    for l in lineas:
        for m in re.finditer(r"([A-Za-zÁÉÍÓÚÑáéíóúñ][A-Za-zÁÉÍÓÚÑáéíóúñ .]{1,24})\s*:\s*(\S[^:]*?)"
                             r"(?=\s{2,}[A-Za-zÁÉÍÓÚÑ][\w .]{1,24}:|$)", l):
            campo, valor = m.group(1).strip(), m.group(2).strip()
            if not ya_usadas.search(campo) and len(valor) <= 80:
                datos.append(DatoAdicional(campo=campo.title(), valor=valor))
    return datos


def extraer_de_texto(texto: str, tablas: list | None = None, metodo: str = "") -> Factura:
    lineas = _lineas(texto)
    f = Factura(texto_completo=texto, metodo_lectura=metodo)

    # Tipo y número
    f.tipo_documento = _buscar(
        r"\b(factura(?:\s+electr[oó]nica)?|nota\s+de\s+cr[eé]dito|nota\s+de\s+d[eé]bito|boleta(?:\s+de\s+venta)?"
        r"|recibo|comprobante(?:\s+de\s+venta)?|ticket)\b", texto)
    if f.tipo_documento:
        f.tipo_documento = f.tipo_documento.title()
    peru = re.search(r"\b([FBE][0-9O]{3})\s*[-–—_~.]+\s*([0-9O]{1,8})\b", texto)  # Perú F001-123
    f.numero_factura = (
        _buscar(r"\b(\d{3}-\d{3}-\d{6,9})\b", texto)                       # Ecuador 001-001-000000123
        or (f"{peru.group(1)[0]}{peru.group(1)[1:].replace('O', '0')}-{peru.group(2).replace('O', '0')}"
            if peru else None)
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

    f.emisor.nombre_comercial, f.emisor.nombre_o_razon_social = _nombres_emisor(lineas)
    fin_valor = r"(?:\s{2,}|\s+(?:R\.?U\.?C|NIT|RFC|C[ÉE]DULA|DNI|ID|FECHA)\b|$)"
    f.receptor.nombre_o_razon_social = _buscar(
        r"^\s*(?:cliente|se[ñn]or(?:\(?es\)?)?|sr\.?\(?es\)?|raz[oó]n\s+social(?:\s*/\s*nombres?)?|adquiriente"
        rf"|comprador|facturar\s+a|denominaci[oó]n)\s*[:.]?\s+([^\n]{{3,80}}?){fin_valor}",
        texto, re.I | re.M) or _buscar(rf"\bnombres?\s*[:.]\s*([^\n]{{3,80}}?){fin_valor}", texto, re.I | re.M)

    # Contacto
    emails = re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", texto)
    telefonos = re.findall(r"(?:tel[ée]?f?o?n?o?|cel(?:ular)?|m[oó]vil|tlf)\.?\s*[:.]?\s*([+\d][\d\s\-()]{6,18}\d)",
                           texto, re.I)
    direcciones = re.findall(
        r"(?:direcci[oó]n|direcc?\.?|dir\.|domicilio)\s*(?:matriz|sucursal|fiscal)?\s*[:.]\s*([^\n]{3,100}?)"
        r"(?=\s{2,}|\s+(?:R\.?U\.?C|FECHA|TEL)\b|$)", texto, re.I | re.M)
    # Dirección del emisor sin etiqueta: línea "Av. …", "Jr. …", "Calle …" en la cabecera.
    dir_cabecera = next((l for l in lineas[:8] if DIRECCION.match(l)), None)
    if dir_cabecera:
        direcciones.insert(0, dir_cabecera)
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
    total_lineas = [l for l in lineas if re.search(r"\btotal\b", l, re.I) and not re.search(
        r"sub\s?-?\s?total|s-total|total\s+(?:iva|igv|impuesto|descuento|dscto)|[ií]tems?|num\.?|n[°º]|cantidad"
        r"|unidades|productos|art[ií]culos|lineas|líneas", l, re.I)]
    for patron in (r"importe\s+total|total\s+a\s+pagar|total\s+general|valor\s+total|monto\s+total|total\s+venta",
                   r"^total\b", r"."):
        for l in total_lineas:
            if re.search(patron, l, re.I) and (v := _ultimo_numero(l)) is not None:
                f.total = v
                break
        if f.total is not None:
            break

    for l in lineas:
        m = re.search(r"\b(IVA|IGV|ISC|ICBPER|ICE|IEPS|ITBIS|ISV|IVU|impuesto|retenci[oó]n)\b", l, re.I)
        if not m or re.search(r"sub\s?total.*\b0\s*%|no\s+objeto|exento", l, re.I):
            continue
        if re.search(r"incluye|inclu|régimen|regimen|agente", l, re.I):
            continue
        monto = _ultimo_numero(l)
        tasa = _buscar(r"(\d{1,2}(?:[.,]\d+)?)\s*%", l)
        if monto is not None and (tasa is None or a_numero(tasa) != monto):
            f.impuestos.append(Impuesto(nombre=m.group(1).upper(), tasa_porcentaje=a_numero(tasa), monto=monto))

    # Control: el total no puede ser menor que el subtotal; si pasa, se recalcula.
    suma_imp = sum(i.monto or 0 for i in f.impuestos)
    if f.subtotal is not None and (f.total is None or f.total < f.subtotal):
        f.total = round(f.subtotal + suma_imp - (f.total_descuentos or 0), 2)

    f.total_en_letras = _buscar(r"^\s*son\s*[:.]?\s*([^\n]{5,120})", texto, re.I | re.M)

    # Pago y autorización
    f.forma_de_pago = (
        _buscar(r"(?:forma|m[eé]todo|medio|condici[oó]n)\s+de\s+pago\s*[:.]?\s*([^\n]{3,60})", texto)
        or _buscar(r"\b((?:venta\s+al\s+)?(?:contado|cr[eé]dito)(?:\s+\d+\s+d[ií]as)?)\b", texto)
        or _buscar(r"\b(efectivo|tarjeta(?:\s+de\s+(?:cr[eé]dito|d[eé]bito))?|transferencia|yape|plin|dep[oó]sito)\b",
                   texto))
    f.datos_adicionales += _datos_etiquetados(lineas)
    cancelado = _fecha_cerca(texto, r"cancelado|pagado")
    if cancelado:
        f.datos_adicionales.append(DatoAdicional(campo="Fecha de pago", valor=normalizar_fecha(cancelado)))
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
