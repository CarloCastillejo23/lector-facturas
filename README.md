🧾 Lector de facturas
Aplicación web en Streamlit: subes facturas en PDF o foto y descargas sus datos en Excel. No usa APIs de pago:

PDF digital → lee el texto y las tablas directamente (pdfplumber).
Foto o PDF escaneado → OCR gratuito con Tesseract.
Reglas automáticas buscan: número, fechas, emisor y cliente (RUC, NIT, RFC, CUIT, RUT, RIF, DNI), dirección, teléfono, correo, ítems, subtotal, impuestos (IVA, IGV…), total, forma de pago y autorización.
El Excel trae las hojas Facturas, Items, Impuestos, Otros datos y Texto completo.

Publicar gratis en Streamlit Community Cloud
Sube esta carpeta a un repositorio de GitHub.
Entra a https://share.streamlit.io e inicia sesión con GitHub.
Create app → elige el repositorio, rama main y archivo app.py → Deploy.
packages.txt instala Tesseract (con español) y Poppler en el servidor automáticamente.

Ejecutar en tu PC (opcional)
pip install -r requirements.txt
streamlit run app.py
Para fotos y PDFs escaneados en Windows también necesitas Tesseract (marca el idioma español al instalar) y Poppler en el PATH. Los PDFs digitales funcionan sin ellos.

Ajustar a tus facturas
Las reglas están en extractor.py. Si un dato no sale bien en tus facturas, agrega o ajusta el patrón correspondiente (por ejemplo, en IDENTIFICACIONES o en extraer_de_texto).
