import os
import json
import time
import sqlite3
from flask import Flask, render_template, request, jsonify, Response
from PIL import Image
import pypdfium2 as pdfium
from google import genai
from google.genai import types

# ----------------- 1. INICIALIZACIÓN DE FLASK -----------------
app = Flask(__name__)

DB_FILE = "cronogramas.db"
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ----------------- 2. BASE DE DATOS LOCAL -----------------
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS guias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT UNIQUE NOT NULL
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS actividades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guia_id INTEGER,
            tipo TEXT,
            titulo TEXT,
            cuidadora TEXT,
            dia INTEGER,
            mes INTEGER,
            anio INTEGER,
            hora_inicio TEXT,
            hora_fin TEXT,
            ubicacion TEXT,
            FOREIGN KEY(guia_id) REFERENCES guias(id)
        )
    ''')
    conn.commit()
    conn.close()

init_db()
# ----------------- 3. EXTRACCIÓN CON GEMINI VISION -----------------
def extraer_datos_con_vision(file_path):
    print(f"--> [1/3] Procesando archivo: {file_path}")
    api_key = os.environ.get("GEMINI_API_KEY", "AQ.Ab8RN6I-5RxuQM7icyvIW5XN_-jO5jfMMpLGowopuiQhMvEZ4g")
    client = genai.Client(api_key=api_key)
    
    if file_path.lower().endswith(".pdf"):
        print("--> [2/3] Renderizando primera página del PDF a imagen...")
        pdf = pdfium.PdfDocument(file_path)
        page = pdf[0]
        pil_image = page.render(scale=2).to_pil()
    else:
        print("--> [2/3] Abriendo imagen...")
        pil_image = Image.open(file_path)

    prompt = """
    Analiza esta ficha de cronograma mensual del Programa Cuna Más (Formato UTAI-FR-164).
    Extrae la información en un formato JSON estricto con las siguientes claves:
    {
      "guia_familia": "Nombre completo de la Guía de Familia (busca en el encabezado)",
      "mes": "Mes indicado (ej. SETIEMBRE)",
      "actividades": [
        {
          "tipo": "VISITA" o "CIAI",
          "dia": número de día (entero, ej. 1, 2, 14, 21),
          "hora": "formato HH:MM AM/PM tal como está escrito",
          "nino": "Nombre y apellidos del niño/niña",
          "cuidadora": "Nombre y apellidos de la cuidadora",
          "ubicacion": "Nombre del CIAI si es tipo CIAI, o vacío si es visita"
        }
      ]
    }
    Instrucciones críticas:
    1. Si una fila tiene dos fechas/horas escritas a mano (ej. 02 y 16), genera dos objetos separados en la lista de actividades.
    2. Lee con sumo cuidado los números de la columna VISITA DOMICILIARIA (FECHA y HORA) y SESIONES EN EL CIAI (FECHA, HORA y CIAI).
    3. Responde ÚNICAMENTE con el bloque JSON válido, sin explicaciones ni markdown envolvente.
    """

    # Modelo 100% gratuito con soporte de visión
    modelo = 'gemini-3.6-flash'
    max_intentos = 3

    for intento in range(max_intentos):
        try:
            print(f"--> [3/3] Extrayendo datos con {modelo} (Intento {intento + 1})...")
            response = client.models.generate_content(
                model=modelo,
                contents=[pil_image, prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                )
            )
            return json.loads(response.text)
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                print("--> Cuota por minuto alcanzada. Esperando 15 segundos antes de reintentar...")
                time.sleep(15)
            elif "503" in error_msg or "UNAVAILABLE" in error_msg:
                print("--> Servidor ocupado. Esperando 5 segundos...")
                time.sleep(5)
            else:
                raise e

    raise Exception("Se agotaron los reintentos debido a límites de cuota temporal de la API.")
# ----------------- 3. EXTRACCIÓN CON GEMINI VISION -----------------
    # Modelos recomendados y solicitados directamente por tu cuenta de Google API
    modelos_disponibles = ['gemini-3.6-flash', 'gemini-3.1-pro-preview']
    ultimo_error = None

    for modelo in modelos_disponibles:
        try:
            print(f"--> [3/3] Intentando procesar con modelo: {modelo}...")
            response = client.models.generate_content(
                model=modelo,
                contents=[pil_image, prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"
                )
            )
            return json.loads(response.text)
        except Exception as e:
            print(f"Aviso: Error con el modelo {modelo}: {e}. Intentando alternativa...")
            ultimo_error = e
            time.sleep(2)

    raise ultimo_error

# ----------------- 4. RUTAS WEB Y ENDPOINTS -----------------
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/guias', methods=['GET'])
def get_guias():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, nombre FROM guias ORDER BY nombre ASC")
    guias = [{"id": row[0], "nombre": row[1]} for row in c.fetchall()]
    conn.close()
    return jsonify(guias)

@app.route('/api/cargar-documento', methods=['POST'])
def cargar_documento():
    if 'file' not in request.files:
        return jsonify({"error": "No se envió ningún archivo"}), 400
    
    file = request.files['file']
    save_path = os.path.join(UPLOAD_FOLDER, file.filename)
    file.save(save_path)

    try:
        data = extraer_datos_con_vision(save_path)
        nombre_guia = data.get("guia_familia", "Guía Sin Nombre").strip()

        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO guias (nombre) VALUES (?)", (nombre_guia,))
        c.execute("SELECT id FROM guias WHERE nombre = ?", (nombre_guia,))
        guia_id = c.fetchone()[0]

        mes_num = 9
        for act in data.get("actividades", []):
            dia = int(act.get("dia", 1))
            c.execute('''
                INSERT INTO actividades (guia_id, tipo, titulo, cuidadora, dia, mes, anio, hora_inicio, ubicacion)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                guia_id,
                act.get("tipo"),
                act.get("nino"),
                act.get("cuidadora"),
                dia,
                mes_num,
                2026,
                act.get("hora"),
                act.get("ubicacion", "")
            ))
        conn.commit()
        conn.close()

        print(f"--> ¡Registro completado! {len(data.get('actividades', []))} actividades para {nombre_guia}")
        return jsonify({"success": True, "guia": nombre_guia, "total_actividades": len(data.get("actividades", []))})
    except Exception as e:
        print(f"Error al procesar: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/actividades/<int:guia_id>', methods=['GET'])
def get_actividades(guia_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        SELECT id, tipo, titulo, cuidadora, dia, mes, anio, hora_inicio, ubicacion
        FROM actividades WHERE guia_id = ? ORDER BY dia ASC, hora_inicio ASC
    ''', (guia_id,))
    rows = c.fetchall()
    conn.close()

    eventos = []
    for r in rows:
        eventos.append({
            "id": r[0],
            "tipo": r[1],
            "titulo": r[2],
            "cuidadora": r[3],
            "dia": r[4],
            "mes": r[5],
            "anio": r[6],
            "hora": r[7],
            "ubicacion": r[8]
        })
    return jsonify(eventos)

@app.route('/api/asignar-tarea', methods=['POST'])
def asignar_tarea():
    data = request.json
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        INSERT INTO actividades (guia_id, tipo, titulo, cuidadora, dia, mes, anio, hora_inicio, ubicacion)
        VALUES (?, 'OTRA', ?, '', ?, ?, ?, ?, '')
    ''', (
        data['guia_id'],
        data['titulo'],
        data['dia'],
        data['mes'],
        data['anio'],
        data['hora']
    ))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/api/exportar-ics/<int:guia_id>', methods=['GET'])
def exportar_ics(guia_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT nombre FROM guias WHERE id = ?", (guia_id,))
    guia_nombre = c.fetchone()[0]

    c.execute("SELECT tipo, titulo, cuidadora, dia, mes, anio, hora_inicio, ubicacion FROM actividades WHERE guia_id = ?", (guia_id,))
    actividades = c.fetchall()
    conn.close()

    ics = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"X-WR-CALNAME:Cronograma - {guia_nombre}",
        "CALSCALE:GREGORIAN"
    ]
    for act in actividades:
        dia_str = f"{act[3]:02d}"
        mes_str = f"{act[4]:02d}"
        hora_raw = act[6].upper().replace(" ", "")
        hora_val = "090000"
        if "PM" in hora_raw or "PR" in hora_raw:
            digits = hora_raw.split(":")[0]
            val = int(''.join(filter(str.isdigit, digits))) if digits else 2
            hora_val = f"{val + 12:02d}0000" if val < 12 else "120000"
        elif "AM" in hora_raw:
            digits = hora_raw.split(":")[0]
            val = int(''.join(filter(str.isdigit, digits))) if digits else 9
            hora_val = f"{val:02d}0000"

        dtstart = f"{act[5]}{mes_str}{dia_str}T{hora_val}"
        ics.extend([
            "BEGIN:VEVENT",
            f"SUMMARY:[{act[0]}] {act[1]}",
            f"DESCRIPTION:Cuidadora: {act[2]} | Ubicacion: {act[7]}",
            f"DTSTART:{dtstart}",
            f"DTEND:{dtstart}",
            "END:VEVENT"
        ])
    ics.append("END:VCALENDAR")

    return Response(
        "\r\n".join(ics),
        mimetype="text/calendar",
        headers={"Content-Disposition": f"attachment;filename=cronograma_{guia_nombre}.ics"}
    )

# ----------------- 5. INICIO DEL SERVIDOR -----------------
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)