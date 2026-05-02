from flask import (
    Flask, render_template, request,
    redirect, url_for, session
)
from functools import wraps
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
import os
import re
import logging
from dotenv import load_dotenv
from bot_wa import procesar as wa_procesar

# ─── Config ──────────────────────────────────────────────────────
load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = Flask(
    __name__,
    static_folder="static",
    static_url_path="/static",
    template_folder="templates"
)

app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-cambiar-en-produccion")

ADMIN_USER     = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "tecnomedic2025")

# ─── WhatsApp / Twilio ───────────────────────────────────────────
TWILIO_SID     = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN   = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_WA_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")
WA_ENABLED     = bool(TWILIO_SID and TWILIO_TOKEN)

# ─── Google Sheets ───────────────────────────────────────────────
scope  = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]
creds  = ServiceAccountCredentials.from_json_keyfile_name("credenciales.json", scope)
client = gspread.authorize(creds)
sheet  = client.open("Turnos TECNOMEDIC").sheet1


# ─── WhatsApp helpers ────────────────────────────────────────────

def formatear_telefono_wa(telefono: str) -> str:
    """
    Convierte un teléfono argentino al formato WhatsApp:
    whatsapp:+549XXXXXXXXXX
    Acepta: 3794123456 / +543794123456 / 549... / 011...
    """
    digitos = re.sub(r"\D", "", telefono)
    if digitos.startswith("54"):
        if not digitos.startswith("549"):
            digitos = "549" + digitos[2:]
    elif digitos.startswith("0"):
        digitos = "549" + digitos[1:]
    else:
        digitos = "549" + digitos
    return f"whatsapp:+{digitos}"

def enviar_whatsapp(telefono: str, mensaje: str) -> bool:
    """Envía un mensaje de WhatsApp via Twilio. Retorna True si exitoso."""
    if not WA_ENABLED:
        log.warning("WhatsApp no configurado — credenciales Twilio faltantes en .env")
        return False
    try:
        wa_to = formatear_telefono_wa(telefono)
        resp  = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json",
            data={"From": TWILIO_WA_FROM, "To": wa_to, "Body": mensaje},
            auth=(TWILIO_SID, TWILIO_TOKEN),
            timeout=10
        )
        if resp.status_code == 201:
            log.info(f"WhatsApp enviado a {wa_to}")
            return True
        log.error(f"Error Twilio {resp.status_code}: {resp.text}")
        return False
    except Exception as e:
        log.error(f"Excepción enviando WhatsApp: {e}")
        return False

# ─── Decorador login ─────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# ─── Autenticación ───────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        user = request.form.get("usuario", "").strip()
        pwd  = request.form.get("password", "").strip()
        if user == ADMIN_USER and pwd == ADMIN_PASSWORD:
            session["logged_in"] = True
            session["usuario"]   = user
            return redirect(url_for("admin"))
        error = "Usuario o contraseña incorrectos."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ─── Rutas públicas ──────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/turnos")
def turnos():
    rows = sheet.get_all_values()
    taken_dates = []
    if len(rows) > 1:
        for row in rows[1:]:
            estado = row[5] if len(row) > 5 else ""
            fecha = row[3] if len(row) > 3 else ""
            if estado.strip() in ("Confirmado", "Pendiente") and fecha.strip():
                taken_dates.append(fecha.strip())
    return render_template("form.html", taken_dates=taken_dates)


@app.route("/guardar", methods=["POST"])
def guardar():
    data = {
        "nombre":   request.form["nombre"],
        "telefono": request.form["telefono"],
        "email":    request.form["email"],
        "fecha":    request.form["fecha"],
        "hora":     request.form["hora"]
    }

    # 1 — Webhook n8n (email automático)
    try:
        requests.post(
            os.environ.get(
                "N8N_WEBHOOK_URL",
                "http://localhost:5678/webhook-test/turnos-tecnomedic"
            ),
            json=data,
            timeout=3
        )
    except Exception:
        pass

    # 2 — Guardar en Google Sheets
    sheet.append_row([
        data["nombre"], data["telefono"], data["email"],
        data["fecha"],  data["hora"],     "Pendiente"
    ])

    # 3 — WhatsApp: notificación de solicitud recibida
    msg_solicitud = (
        f"✅ *TECNOMEDIC* - Turno recibido\n\n"
        f"Hola {data['nombre']}! 👋\n"
        f"Recibimos tu solicitud para *Cámara Hiperbárica*.\n\n"
        f"📅 Fecha: {data['fecha']}\n"
        f"⏰ Hora: {data['hora']}\n\n"
        f"Te confirmaremos a la brevedad. ¡Gracias!"
    )
    enviar_whatsapp(data["telefono"], msg_solicitud)

    return render_template("confirmacion.html", turno=data)


# ─── Rutas protegidas ────────────────────────────────────────────

@app.route("/admin")
@login_required
def admin():
    try:
        rows = sheet.get_all_values()
        if not rows:
            return render_template("admin.html", turnos=[],
                                   total=0, confirmados=0, pendientes=0)
        headers = rows[0]
        turnos  = []
        for i, row in enumerate(rows[1:]):
            if len(row) < len(headers):
                row += [""] * (len(headers) - len(row))
            turno        = dict(zip(headers, row))
            turno["row"] = i + 2
            turnos.append(turno)

        total       = len(turnos)
        confirmados = sum(1 for t in turnos if t.get("Estado") == "Confirmado")
        pendientes  = sum(1 for t in turnos if t.get("Estado") == "Pendiente")

        return render_template("admin.html", turnos=turnos,
                               total=total,
                               confirmados=confirmados,
                               pendientes=pendientes)
    except Exception as e:
        return f"Error al leer la hoja: {str(e)}"


@app.route("/actualizar", methods=["POST"])
@login_required
def actualizar():
    row      = int(request.form["row"])
    estado   = request.form["estado"]
    sheet.update_cell(row, 6, estado)

    # WhatsApp al confirmar el turno
    if estado == "Confirmado":
        try:
            all_rows = sheet.get_all_values()
            fila     = all_rows[row - 1]
            nombre, telefono, _, fecha, hora = fila[0], fila[1], fila[2], fila[3], fila[4]
            msg_confirmado = (
                f"🎉 *TECNOMEDIC* - Turno confirmado\n\n"
                f"Hola {nombre}! Tu turno fue *CONFIRMADO* ✔️\n\n"
                f"📅 {fecha}  ⏰ {hora}\n\n"
                f"📍 C. Pellegrini 799, Corrientes\n"
                f"📞 (3794) 34-9278\n\n"
                f"¡Te esperamos!"
            )
            enviar_whatsapp(telefono, msg_confirmado)
        except Exception as e:
            log.error(f"Error enviando WA de confirmación: {e}")

    return redirect(url_for("admin"))


@app.route("/modificar", methods=["POST"])
@login_required
def modificar():
    row      = int(request.form["row"])
    nombre   = request.form["nombre"]
    telefono = request.form["telefono"]
    email    = request.form["email"]
    fecha    = request.form["fecha"]
    hora     = request.form["hora"]
    estado   = request.form["estado"]

    sheet.update_cell(row, 1, nombre)
    sheet.update_cell(row, 2, telefono)
    sheet.update_cell(row, 3, email)
    sheet.update_cell(row, 4, fecha)
    sheet.update_cell(row, 5, hora)
    sheet.update_cell(row, 6, estado)

    # WhatsApp: aviso de turno modificado
    msg_modificado = (
        f"✏️ *TECNOMEDIC* - Turno actualizado\n\n"
        f"Hola {nombre}! Tu turno fue *modificado*.\n\n"
        f"📅 Nueva fecha: {fecha}\n"
        f"⏰ Nueva hora: {hora}\n"
        f"📋 Estado: {estado}\n\n"
        f"Cualquier consulta llamanos al (3794) 34-9278."
    )
    enviar_whatsapp(telefono, msg_modificado)

    return redirect(url_for("admin"))


# ─── Bot WhatsApp ─────────────────────────────────────────────────
@app.route("/whatsapp/bot", methods=["POST"])
def whatsapp_bot():
    """
    Webhook que recibe mensajes entrantes de WhatsApp via Twilio.
    Twilio hace POST con los campos: From, Body, etc.
    Respondemos con TwiML vacío (el bot responde con _enviar() directamente).
    """
    phone = request.form.get("From", "").strip()
    msg   = request.form.get("Body", "").strip()

    if not phone or not msg:
        return '<Response></Response>', 200, {'Content-Type': 'text/xml'}

    log.info(f"WA recibido de {phone}: {msg[:60]}")

    try:
        wa_procesar(phone, msg, sheet)
    except Exception as e:
        log.error(f"Error en bot WA: {e}")

    return '<Response></Response>', 200, {'Content-Type': 'text/xml'}


# ─── Main ────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=True)
