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

# ── Credenciales — siempre desde variables de entorno ─────────────
ADMIN_USER     = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")
GMAIL_USER     = os.environ.get("GMAIL_USER", "")
GMAIL_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
TWILIO_SID     = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN   = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_WA_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

# ── n8n DESACTIVADO — emails van por SMTP directo ─────────────────
# Para reactivar: descomentar N8N_WEBHOOK_URL en Render Environment
# y el bloque "n8n webhook" dentro de /guardar
# N8N_WEBHOOK_URL = os.environ.get("N8N_WEBHOOK_URL", "")

# ── Horarios: 1h15min · Mañana 8:30-13:00 · Tarde 16:30-20:30 ────
HORARIOS        = ["08:30", "09:45", "11:00", "16:30", "17:45", "19:00"]
MAX_POR_HORARIO = 2

# ── Columnas Google Sheets (0-based para Python, 1-based para gspread)
# Nombre(1)|Apellido(2)|DNI(3)|ObraSocial(4)|Telefono(5)|Email(6)|Fecha(7)|Hora(8)|Estado(9)
IDX = {
    "nombre":     0,
    "apellido":   1,
    "dni":        2,
    "obra_social":3,
    "telefono":   4,
    "email":      5,
    "fecha":      6,
    "hora":       7,
    "estado":     8,
}
COL = {k: v + 1 for k, v in IDX.items()}

COLS_CANON = ['Nombre','Apellido','DNI','ObraSocial','Telefono','Email','Fecha','Hora','Estado']

# ── Google Sheets ──────────────────────────────────────────────────
scope   = ["https://spreadsheets.google.com/feeds",
           "https://www.googleapis.com/auth/drive"]
creds   = ServiceAccountCredentials.from_json_keyfile_name("credenciales.json", scope)
gclient = gspread.authorize(creds)
sheet   = gclient.open("Turnos TECNOMEDIC").sheet1

# ── Bot WhatsApp ───────────────────────────────────────────────────
try:
    from bot_wa import procesar as wa_procesar
    BOT_OK = True
    log.info("✅ bot_wa importado correctamente")
except Exception as e:
    BOT_OK = False
    log.error(f"❌ No se pudo importar bot_wa: {e}")


# ══════════════════════════════════════════════════════════════════
# EMAIL — SMTP directo a Gmail
# ══════════════════════════════════════════════════════════════════

def enviar_email(destinatario: str, asunto: str, cuerpo: str) -> bool:
    if not GMAIL_USER or not GMAIL_PASSWORD:
        log.warning("⚠️ EMAIL NO CONFIGURADO — faltan GMAIL_USER o GMAIL_APP_PASSWORD en Render → Environment")
        return False
    try:
        log.info(f"📧 Enviando email → {destinatario} | desde: {GMAIL_USER}")
        msg = MIMEMultipart("alternative")
        msg["Subject"] = asunto
        msg["From"]    = f"TECNOMEDIC <{GMAIL_USER}>"
        msg["To"]      = destinatario
        msg.attach(MIMEText(cuerpo, "plain", "utf-8"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as s:
            s.login(GMAIL_USER, GMAIL_PASSWORD)
            s.sendmail(GMAIL_USER, destinatario, msg.as_string())
        log.info(f"✅ Email enviado a {destinatario}")
        return True
    except smtplib.SMTPAuthenticationError as e:
        log.error(f"❌ Autenticación Gmail fallida: {e}")
        log.error("   → Verificar que GMAIL_APP_PASSWORD sea el App Password de Google (16 chars sin espacios)")
        log.error("   → Verificar que la cuenta tenga verificación en 2 pasos activada")
        return False
    except smtplib.SMTPException as e:
        log.error(f"❌ Error SMTP: {e}")
        return False
    except Exception as e:
        log.error(f"❌ Error inesperado email [{type(e).__name__}]: {e}")
        return False

def email_solicitud(data: dict):
    nombre = f"{data.get('nombre','')} {data.get('apellido','')}".strip()
    enviar_email(
        data["email"],
        "Solicitud de turno recibida – TECNOMEDIC",
        f"Hola {nombre},\n\n"
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


# ══════════════════════════════════════════════════════════════════
# WHATSAPP — Twilio
# ══════════════════════════════════════════════════════════════════

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
    if not TWILIO_SID or not TWILIO_TOKEN:
        log.warning("⚠️ WHATSAPP NO CONFIGURADO — faltan TWILIO_ACCOUNT_SID o TWILIO_AUTH_TOKEN en Render")
        return False
    try:
        wa_to = formatear_wa(telefono)
        log.info(f"📱 Enviando WA → {wa_to}")
        r = http_req.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json",
            data={"From": TWILIO_WA_FROM, "To": wa_to, "Body": mensaje},
            auth=(TWILIO_SID, TWILIO_TOKEN), timeout=10
        )
        if r.status_code == 201:
            log.info(f"✅ WhatsApp enviado a {wa_to}")
            return True
        log.error(f"❌ Twilio error {r.status_code}: {r.text}")
        return False
    except Exception as e:
        log.error(f"❌ Error enviando WhatsApp [{type(e).__name__}]: {e}")
        return False


# ══════════════════════════════════════════════════════════════════
# SLOTS
# ══════════════════════════════════════════════════════════════════

def get_ocupados(fecha_hoja: str) -> dict:
    """Retorna {hora: cantidad} para fecha DD/MM/YYYY. Ignora Cancelado."""
    conteo = {h: 0 for h in HORARIOS}
    try:
        rows = sheet.get_all_values()
        if len(rows) < 2: return conteo
        i_f = IDX["fecha"]
        i_h = IDX["hora"]
        i_e = IDX["estado"]
        for row in rows[1:]:
            if len(row) <= i_e: continue
            if row[i_f].strip() != fecha_hoja: continue
            if row[i_e].strip().lower() == "cancelado": continue
            hora = row[i_h].strip()
            if hora in conteo: conteo[hora] += 1
    except Exception as e:
        log.error(f"❌ Error get_ocupados: {e}")
    return conteo


# ══════════════════════════════════════════════════════════════════
# LOGIN
# ══════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════
# RUTAS PÚBLICAS
# ══════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/turnos")
def turnos():
    return render_template("form.html")

@app.route("/api/horarios")
def api_horarios():
    from datetime import datetime as dt
    fecha_raw = request.args.get("fecha", "")
    if not fecha_raw:
        return jsonify({"error": "fecha requerida"}), 400
    try:
        fecha_hoja = dt.strptime(fecha_raw, "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return jsonify({"error": "formato inválido, usar YYYY-MM-DD"}), 400
    ocupados = get_ocupados(fecha_hoja)
    slots = []
    for h in HORARIOS:
        c = ocupados.get(h, 0)
        slots.append({
            "hora": h, "ocupados": c, "max": MAX_POR_HORARIO,
            "disponible": c < MAX_POR_HORARIO, "libres": MAX_POR_HORARIO - c
        })
    return jsonify({"fecha": fecha_hoja, "slots": slots})


@app.route("/guardar", methods=["POST"])
def guardar():
    data = {
        "nombre":      request.form.get("nombre", "").strip(),
        "apellido":    request.form.get("apellido", "").strip(),
        "dni":         request.form.get("dni", "").strip(),
        "obra_social": request.form.get("obra_social", "").strip(),
        "telefono":    request.form.get("telefono", "").strip(),
        "email":       request.form.get("email", "").strip(),
        "fecha":       request.form.get("fecha", "").strip(),
        "hora":        request.form.get("hora", "").strip(),
    }
    if not all(data[k] for k in ["nombre","apellido","telefono","email","fecha","hora"]):
        return render_template("form.html", error="Por favor completá todos los campos obligatorios.")

    ocupados = get_ocupados(data["fecha"])
    if ocupados.get(data["hora"], 0) >= MAX_POR_HORARIO:
        return render_template("form.html", error="Ese horario ya no tiene lugares. Por favor elegí otro.")

    # Guardar: Nombre|Apellido|DNI|ObraSocial|Telefono|Email|Fecha|Hora|Estado
    sheet.append_row([
        data["nombre"], data["apellido"], data["dni"], data["obra_social"],
        data["telefono"], data["email"], data["fecha"], data["hora"], "Pendiente"
    ])
    log.info(f"✅ Turno guardado: {data['nombre']} {data['apellido']} | {data['fecha']} {data['hora']}")

    # ── n8n webhook (DESACTIVADO) ────────────────────────────────
    # try:
    #     http_req.post(N8N_WEBHOOK_URL, json=data, timeout=3)
    # except Exception:
    #     pass

    try: email_solicitud(data)
    except Exception as e: log.error(f"❌ Error email solicitud: {e}")

    try:
        enviar_whatsapp(data["telefono"],
            f"✅ *TECNOMEDIC* – Turno recibido\n\n"
            f"Hola {data['nombre']}! 👋\n"
            f"📅 {data['fecha']}  ⏰ {data['hora']}\n\n"
            f"Te confirmaremos a la brevedad. ¡Gracias!"
        )
    except Exception as e: log.error(f"❌ Error WA solicitud: {e}")

    return render_template("confirmacion.html", turno=data)


# ══════════════════════════════════════════════════════════════════
# ADMIN
# ══════════════════════════════════════════════════════════════════

@app.route("/admin")
@login_required
def admin():
    try:
        rows = sheet.get_all_values()
        if not rows:
            return render_template("admin.html", turnos=[], total=0, confirmados=0, pendientes=0)
        headers = rows[0]
        turnos  = []
        for i, row in enumerate(rows[1:]):
            row_ext = list(row) + [""] * max(0, len(COLS_CANON) - len(row))
            t = dict(zip(headers, row_ext))
            for idx, key in enumerate(COLS_CANON):
                if key not in t:
                    t[key] = row_ext[idx] if idx < len(row_ext) else ""
            t["row"] = i + 2
            turnos.append(t)
        total       = len(turnos)
        confirmados = sum(1 for t in turnos if t.get("Estado") == "Confirmado")
        pendientes  = sum(1 for t in turnos if t.get("Estado") == "Pendiente")
        return render_template("admin.html", turnos=turnos,
                               total=total, confirmados=confirmados, pendientes=pendientes)
    except Exception as e:
        log.error(f"❌ Error admin: {e}")
        return f"Error al leer la hoja: {e}", 500


@app.route("/actualizar", methods=["POST"])
@login_required
def actualizar():
    row    = int(request.form["row"])
    estado = request.form["estado"]
    sheet.update_cell(row, COL["estado"], estado)
    log.info(f"Estado fila {row} → {estado}")
    if estado == "Confirmado":
        try:
            fila   = sheet.get_all_values()[row - 1]
            nombre = f"{fila[IDX['nombre']]} {fila[IDX['apellido']]}".strip()
            tel    = fila[IDX["telefono"]]
            email  = fila[IDX["email"]]
            fecha  = fila[IDX["fecha"]]
            hora   = fila[IDX["hora"]]
            email_confirmacion(nombre, email, fecha, hora)
            enviar_whatsapp(tel,
                f"🎉 *TECNOMEDIC* – Turno confirmado\n\n"
                f"Hola {fila[IDX['nombre']]}! Tu turno fue *CONFIRMADO* ✔️\n\n"
                f"📅 {fecha}  ⏰ {hora}\n\n"
                f"📍 C. Pellegrini 799, Corrientes\n"
                f"📞 (3794) 34-9278\n\n¡Te esperamos!"
            )
        except Exception as e:
            log.error(f"❌ Error notificando confirmación: {e}")
    return redirect(url_for("admin"))


@app.route("/modificar", methods=["POST"])
@login_required
def modificar():
    row = int(request.form["row"])
    sheet.update_cell(row, COL["nombre"],      request.form.get("nombre", ""))
    sheet.update_cell(row, COL["apellido"],    request.form.get("apellido", ""))
    sheet.update_cell(row, COL["dni"],         request.form.get("dni", ""))
    sheet.update_cell(row, COL["obra_social"], request.form.get("obra_social", ""))
    sheet.update_cell(row, COL["telefono"],    request.form.get("telefono", ""))
    sheet.update_cell(row, COL["email"],       request.form.get("email", ""))
    sheet.update_cell(row, COL["fecha"],       request.form.get("fecha", ""))
    sheet.update_cell(row, COL["hora"],        request.form.get("hora", ""))
    sheet.update_cell(row, COL["estado"],      request.form.get("estado", ""))
    log.info(f"✅ Turno fila {row} modificado")
    return redirect(url_for("admin"))


@app.route("/eliminar", methods=["POST"])
@login_required
def eliminar():
    row = int(request.form["row"])
    sheet.delete_rows(row)
    log.info(f"🗑 Turno fila {row} eliminado")
    return redirect(url_for("admin"))


# ══════════════════════════════════════════════════════════════════
# BOT WHATSAPP
# ══════════════════════════════════════════════════════════════════

@app.route("/whatsapp/bot", methods=["POST"])
def whatsapp_bot():
    phone = request.form.get("From", "").strip()
    msg   = request.form.get("Body", "").strip()
    if not phone or not msg:
        return '<Response></Response>', 200, {'Content-Type': 'text/xml'}
    log.info(f"📱 WA recibido de {phone}: {msg[:60]}")
    if BOT_OK:
        try:
            wa_procesar(phone, msg, sheet)
        except Exception as e:
            log.error(f"❌ Error en bot WA: {e}")
    else:
        log.warning("⚠️ Bot WA no disponible (error de importación)")
    return '<Response></Response>', 200, {'Content-Type': 'text/xml'}


if __name__ == "__main__":
    app.run(debug=True)
