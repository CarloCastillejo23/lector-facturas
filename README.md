🧾 Lector de facturas
Aplicación web en Streamlit: subes facturas en PDF o foto y descargas sus datos en Excel. No usa APIs de pago:

PDF digital → lee el texto y las tablas directamente (pdfplumber).
Foto o PDF escaneado → OCR gratuito con Tesseract.
Reglas automáticas buscan: número, fechas, emisor y cliente (RUC, NIT, RFC, CUIT, RUT, RIF, DNI), dirección, teléfono, correo, ítems, subtotal, impuestos (IVA, IGV…), total, forma de pago y autorización.
El Excel trae las hojas Facturas, Items, Impuestos, Otros datos y Texto completo.

IA opcional: Google Gemini (modo híbrido)
En modo Automático, cada factura se lee primero gratis con OCR. Si algo no cuadra (falta el número, la fecha, el RUC o el total; cantidad × precio ≠ total; los ítems no suman el subtotal o el total), esa factura se envía a Gemini 3.5 Flash-Lite.

Nivel gratuito de Google AI Studio: US$ 0, con límites de solicitudes por minuto/día. Google puede usar los datos enviados para mejorar sus productos.
Nivel de pago: ~US$ 0.003 por factura enviada a la IA; los datos no se usan para mejorar productos.
Crea la key en https://aistudio.google.com → Get API key. Luego en Streamlit Cloud: Manage app → Settings → Secrets y pega:

GEMINI_API_KEY = "AIza..."
La API key nunca se sube a GitHub. Sin ella, la app funciona igual en modo Solo OCR.

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
