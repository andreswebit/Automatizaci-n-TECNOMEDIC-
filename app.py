from flask import (
    Flask, render_template, request,
    redirect, url_for, session, jsonify
)
from functools import wraps
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests as http_req
import os, re, logging, smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = Flask(__name__, static_folder="static",
            static_url_path="/static", template_folder="templates")
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")

ADMIN_USER     = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "tecnomedic2025")
GMAIL_USER     = os.environ.get("GMAIL_USER", "")
GMAIL_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
TWILIO_SID     = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN   = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_WA_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
WA_ENABLED     = bool(TWILIO_SID and TWILIO_TOKEN)

# Horarios: sesiones 1h15m · mañana 8:30-13:00 · tarde 16:30-20:30
HORARIOS        = ["08:30", "09:45", "11:00", "16:30", "17:45", "19:00"]
MAX_POR_HORARIO = 2   # dos equipos hiperbáricos

# ── Google Sheets ─────────────────────────────────────────────────
# Columnas actuales: Nombre|Telefono|Email|Fecha|Hora|Estado
scope  = ["https://spreadsheets.google.com/feeds",
          "https://www.googleapis.com/auth/drive"]
creds  = ServiceAccountCredentials.from_json_keyfile_name("credenciales.json", scope)
gclient = gspread.authorize(creds)
sheet   = gclient.open("Turnos TECNOMEDIC").sheet1

# ── Import bot WA (seguro, no rompe si falla) ─────────────────────
try:
    from bot_wa import procesar as wa_procesar
    BOT_OK = True
except Exception as e:
    log.error(f"No se pudo importar bot_wa: {e}")
    BOT_OK = False


# ─────────────────────────────────────────────────────────────────
# EMAIL
# ─────────────────────────────────────────────────────────────────

def enviar_email(destinatario: str, asunto: str, cuerpo: str) -> bool:
    if not GMAIL_USER or not GMAIL_PASSWORD:
        log.warning("⚠️ Email no configurado: faltan GMAIL_USER o GMAIL_APP_PASSWORD en las variables de entorno")
        return False
    try:
        log.info(f"📧 Enviando email a {destinatario} desde {GMAIL_USER}")
        msg = MIMEMultipart("alternative")
        msg["Subject"] = asunto
        msg["From"]    = f"TECNOMEDIC <{GMAIL_USER}>"
        msg["To"]      = destinatario
        msg.attach(MIMEText(cuerpo, "plain", "utf-8"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as s:
            s.login(GMAIL_USER, GMAIL_PASSWORD)
            s.sendmail(GMAIL_USER, destinatario, msg.as_string())
        log.info(f"✅ Email enviado exitosamente a {destinatario}")
        return True
    except smtplib.SMTPAuthenticationError as e:
        log.error(f"❌ Error de autenticación Gmail: {e}. Verificar GMAIL_USER y GMAIL_APP_PASSWORD (sin espacios)")
        return False
    except smtplib.SMTPException as e:
        log.error(f"❌ Error SMTP: {e}")
        return False
    except Exception as e:
        log.error(f"❌ Error inesperado enviando email: {type(e).__name__}: {e}")
        return False

def email_solicitud(data: dict):
    enviar_email(
        data["email"],
        "Solicitud de turno recibida – TECNOMEDIC",
        f"Hola {data['nombre']},\n\n"
        f"Recibimos tu solicitud de turno para Cámara Hiperbárica.\n\n"
        f"📅 Fecha: {data['fecha']}\n"
        f"⏰ Hora:  {data['hora']}\n\n"
        f"Te confirmaremos a la brevedad.\n\n"
        f"TECNOMEDIC · C. Pellegrini 799 · Corrientes · (3794) 34-9278"
    )

def email_confirmacion(nombre: str, email: str, fecha: str, hora: str):
    enviar_email(
        email,
        "✔️ Turno confirmado – TECNOMEDIC",
        f"Hola {nombre},\n\n"
        f"Tu turno fue CONFIRMADO ✔️\n\n"
        f"📅 {fecha}  ⏰ {hora}\n\n"
        f"📍 C. Pellegrini 799, Corrientes\n"
        f"📞 (3794) 34-9278\n\n"
        f"¡Te esperamos!"
    )


# ─────────────────────────────────────────────────────────────────
# WHATSAPP
# ─────────────────────────────────────────────────────────────────

def formatear_wa(telefono: str) -> str:
    d = re.sub(r"\D", "", telefono)
    if d.startswith("54"):
        if not d.startswith("549"): d = "549" + d[2:]
    elif d.startswith("0"):
        d = "549" + d[1:]
    else:
        d = "549" + d
    return f"whatsapp:+{d}"

def enviar_whatsapp(telefono: str, mensaje: str) -> bool:
    if not WA_ENABLED: return False
    try:
        r = http_req.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json",
            data={"From": TWILIO_WA_FROM, "To": formatear_wa(telefono), "Body": mensaje},
            auth=(TWILIO_SID, TWILIO_TOKEN), timeout=10
        )
        if r.status_code != 201:
            log.error(f"Twilio {r.status_code}: {r.text}")
        return r.status_code == 201
    except Exception as e:
        log.error(f"WA error: {e}"); return False


# ─────────────────────────────────────────────────────────────────
# LÓGICA DE SLOTS
# ─────────────────────────────────────────────────────────────────

def get_ocupados(fecha_hoja: str) -> dict:
    """Retorna {hora: cantidad_reservas} para fecha DD/MM/YYYY."""
    conteo = {h: 0 for h in HORARIOS}
    try:
        rows = sheet.get_all_values()
        if len(rows) < 2: return conteo
        hdrs = rows[0]
        # Detectar columnas por nombre, fallback a índice
        i_f = hdrs.index("Fecha")  if "Fecha"  in hdrs else 3
        i_h = hdrs.index("Hora")   if "Hora"   in hdrs else 4
        i_e = hdrs.index("Estado") if "Estado" in hdrs else 5
        for row in rows[1:]:
            if len(row) <= max(i_f, i_h, i_e): continue
            if row[i_f].strip() != fecha_hoja: continue
            if row[i_e].strip().lower() == "cancelado": continue
            hora = row[i_h].strip()
            if hora in conteo: conteo[hora] += 1
    except Exception as e:
        log.error(f"Error get_ocupados: {e}")
    return conteo


# ─────────────────────────────────────────────────────────────────
# LOGIN
# ─────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        u = request.form.get("usuario", "").strip()
        p = request.form.get("password", "").strip()
        if u == ADMIN_USER and p == ADMIN_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("admin"))
        error = "Usuario o contraseña incorrectos."
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ─────────────────────────────────────────────────────────────────
# RUTAS PÚBLICAS
# ─────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/turnos")
def turnos():
    return render_template("form.html")

@app.route("/api/horarios")
def api_horarios():
    """GET /api/horarios?fecha=YYYY-MM-DD → JSON disponibilidad."""
    fecha_raw = request.args.get("fecha", "")
    if not fecha_raw:
        return jsonify({"error": "fecha requerida"}), 400
    try:
        from datetime import datetime as dt
        fecha_hoja = dt.strptime(fecha_raw, "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return jsonify({"error": "formato inválido"}), 400

    ocupados = get_ocupados(fecha_hoja)
    slots = []
    for h in HORARIOS:
        c = ocupados.get(h, 0)
        slots.append({
            "hora": h, "ocupados": c, "max": MAX_POR_HORARIO,
            "disponible": c < MAX_POR_HORARIO,
            "libres": MAX_POR_HORARIO - c
        })
    return jsonify({"fecha": fecha_hoja, "slots": slots})

@app.route("/guardar", methods=["POST"])
def guardar():
    data = {
        "nombre":      request.form.get("nombre", "").strip(),
        "apellido":    request.form.get("apellido", "").strip(),
        "dni":         request.form.get("dni", "").strip(),
        "obra_social": request.form.get("obra_social", "").strip(),
        "particular":  "si" if request.form.get("particular") else "",
        "telefono":    request.form.get("telefono", "").strip(),
        "email":       request.form.get("email", "").strip(),
        "fecha":       request.form.get("fecha", "").strip(),   # DD/MM/YYYY
        "hora":        request.form.get("hora", "").strip(),
    }

    if not all([data["nombre"], data["apellido"], data["telefono"],
                data["email"], data["fecha"], data["hora"]]):
        return render_template("form.html",
            error="Por favor completá todos los campos obligatorios.")

    # Validar slot disponible (doble check servidor)
    ocupados = get_ocupados(data["fecha"])
    if ocupados.get(data["hora"], 0) >= MAX_POR_HORARIO:
        return render_template("form.html",
            error="Ese horario ya no tiene lugares disponibles. Por favor elegí otro.")

    nombre_completo = f"{data['nombre']} {data['apellido']}".strip()

    # Guardar en Sheets
    # Columnas: Nombre | Apellido | DNI | ObraSocial | Particular | Telefono | Email | Fecha | Hora | Estado
    sheet.append_row([
        data["nombre"], data["apellido"], data["dni"], data["obra_social"],
        "Particular" if data["particular"] else "",
        data["telefono"], data["email"], data["fecha"], data["hora"], "Pendiente"
    ])
    log.info(f"✅ Turno guardado: {nombre_completo} {data['fecha']} {data['hora']}")

    try: email_solicitud(data)
    except Exception as e: log.error(f"Error email solicitud: {e}")

    try:
        enviar_whatsapp(data["telefono"],
            f"✅ *TECNOMEDIC* – Turno recibido\n\n"
            f"Hola {data['nombre']}! 👋\n"
            f"📅 {data['fecha']}  ⏰ {data['hora']}\n\n"
            f"Te confirmaremos a la brevedad. ¡Gracias!"
        )
    except Exception as e: log.error(f"Error WA solicitud: {e}")

    return render_template("confirmacion.html", turno=data)


# ─────────────────────────────────────────────────────────────────
# ADMIN
# ─────────────────────────────────────────────────────────────────

@app.route("/admin")
@login_required
def admin():
    try:
        rows = sheet.get_all_values()
        if not rows:
            return render_template("admin.html", turnos=[],
                                   total=0, confirmados=0, pendientes=0)
        headers = rows[0]
        # Orden canónico de columnas (posición 0-based)
        COLS = ['Nombre','Apellido','DNI','ObraSocial','Particular',
                'Telefono','Email','Fecha','Hora','Estado']
        turnos = []
        for i, row in enumerate(rows[1:]):
            # Rellenar si la fila tiene menos columnas que los headers
            row_ext = list(row) + [""] * max(0, len(COLS) - len(row))
            # Construir dict por header real
            t = dict(zip(headers, row_ext))
            # Agregar claves canónicas por posición (fallback robusto)
            for idx, key in enumerate(COLS):
                if key not in t:
                    t[key] = row_ext[idx] if idx < len(row_ext) else ""
            t["row"] = i + 2
            turnos.append(t)
        total       = len(turnos)
        confirmados = sum(1 for t in turnos if t.get("Estado") == "Confirmado")
        pendientes  = sum(1 for t in turnos if t.get("Estado") == "Pendiente")
        return render_template("admin.html", turnos=turnos,
                               total=total, confirmados=confirmados,
                               pendientes=pendientes)
    except Exception as e:
        log.error(f"Error admin: {e}")
        return f"Error al leer la hoja: {e}", 500

@app.route("/actualizar", methods=["POST"])
@login_required
def actualizar():
    row    = int(request.form["row"])
    estado = request.form["estado"]
    # Columnas: Nombre(1)|Apellido(2)|DNI(3)|ObraSocial(4)|Particular(5)
    #           |Telefono(6)|Email(7)|Fecha(8)|Hora(9)|Estado(10)
    sheet.update_cell(row, 10, estado)

    if estado == "Confirmado":
        try:
            fila     = sheet.get_all_values()[row - 1]
            nombre   = f"{fila[0]} {fila[1]}".strip()
            telefono = fila[5]
            email    = fila[6]
            fecha    = fila[7]
            hora     = fila[8]
            email_confirmacion(nombre, email, fecha, hora)
            enviar_whatsapp(telefono,
                f"🎉 *TECNOMEDIC* – Turno confirmado\n\n"
                f"Hola {fila[0]}! Tu turno fue *CONFIRMADO* ✔️\n\n"
                f"📅 {fecha}  ⏰ {hora}\n\n"
                f"📍 C. Pellegrini 799, Corrientes\n"
                f"📞 (3794) 34-9278\n\n¡Te esperamos!"
            )
        except Exception as e:
            log.error(f"Error notificando confirmación: {e}")

    return redirect(url_for("admin"))

@app.route("/modificar", methods=["POST"])
@login_required
def modificar():
    row = int(request.form["row"])
    # Columnas: Nombre(1)|Apellido(2)|DNI(3)|ObraSocial(4)|Particular(5)
    #           |Telefono(6)|Email(7)|Fecha(8)|Hora(9)|Estado(10)
    particular = "Particular" if request.form.get("particular") else ""
    sheet.update_cell(row, 1,  request.form.get("nombre", ""))
    sheet.update_cell(row, 2,  request.form.get("apellido", ""))
    sheet.update_cell(row, 3,  request.form.get("dni", ""))
    sheet.update_cell(row, 4,  request.form.get("obra_social", ""))
    sheet.update_cell(row, 5,  particular)
    sheet.update_cell(row, 6,  request.form.get("telefono", ""))
    sheet.update_cell(row, 7,  request.form.get("email", ""))
    sheet.update_cell(row, 8,  request.form.get("fecha", ""))
    sheet.update_cell(row, 9,  request.form.get("hora", ""))
    sheet.update_cell(row, 10, request.form.get("estado", ""))
    return redirect(url_for("admin"))

@app.route("/eliminar", methods=["POST"])
@login_required
def eliminar():
    row = int(request.form["row"])
    sheet.delete_rows(row)
    return redirect(url_for("admin"))


# ─────────────────────────────────────────────────────────────────
# BOT WHATSAPP
# ─────────────────────────────────────────────────────────────────

@app.route("/whatsapp/bot", methods=["POST"])
def whatsapp_bot():
    phone = request.form.get("From", "").strip()
    msg   = request.form.get("Body", "").strip()
    if not phone or not msg:
        return '<Response></Response>', 200, {'Content-Type': 'text/xml'}
    log.info(f"WA de {phone}: {msg[:60]}")
    if BOT_OK:
        try:
            wa_procesar(phone, msg, sheet)
        except Exception as e:
            log.error(f"Error bot WA: {e}")
    return '<Response></Response>', 200, {'Content-Type': 'text/xml'}


if __name__ == "__main__":
    app.run(debug=True)
