import re, os, requests, logging
from datetime import datetime
from calendar import monthrange

log = logging.getLogger(__name__)

TWILIO_SID     = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN   = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_WA_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

HORARIOS = [f"{h:02d}:00" for h in range(8, 18)]

MENU = (
    "🏥 *TECNOMEDIC*\n\n"
    "1️⃣ Sacar turno\n"
    "2️⃣ Modificar turno\n"
    "3️⃣ Cancelar turno\n"
    "4️⃣ Info y horarios\n\n"
    "_Respondé con el número de opción_"
)
INFO = (
    "ℹ️ *TECNOMEDIC*\n\n"
    "🕐 Horarios: Lun a Vie de 8 a 17hs\n"
    "📍 C. Pellegrini 799, Corrientes\n"
    "📞 (3794) 34-9278\n\n"
    "Escribí *0* para volver al menú."
)

# ─── Sesiones en hoja "Sesiones" del mismo Spreadsheet ───────────
# Columnas: Phone | Step | Nombre | Fecha | Hora | Email | Disp | FilaTurno

def _ws_sesiones(sheet):
    try:
        return sheet.spreadsheet.worksheet("Sesiones")
    except Exception:
        ws = sheet.spreadsheet.add_worksheet(title="Sesiones", rows=500, cols=10)
        ws.append_row(["Phone","Step","Nombre","Fecha","Hora","Email","Disp","FilaTurno"])
        return ws

def _get_session(phone, sheet):
    ws = _ws_sesiones(sheet)
    rows = ws.get_all_values()
    for i, row in enumerate(rows):
        if i == 0:
            continue
        if len(row) > 0 and row[0] == phone:
            disp_raw = row[6] if len(row) > 6 else ""
            return {
                "row_ws":     i + 1,
                "phone":      phone,
                "step":       row[1] if len(row) > 1 else "menu",
                "nombre":     row[2] if len(row) > 2 else "",
                "fecha":      row[3] if len(row) > 3 else "",
                "hora":       row[4] if len(row) > 4 else "",
                "email":      row[5] if len(row) > 5 else "",
                "disp":       disp_raw.split("|") if disp_raw else [],
                "fila_turno": int(row[7]) if len(row) > 7 and row[7].isdigit() else 0,
            }, ws
    ws.append_row([phone, "menu", "", "", "", "", "", ""])
    all_rows = ws.get_all_values()
    return {
        "row_ws": len(all_rows), "phone": phone, "step": "menu",
        "nombre": "", "fecha": "", "hora": "", "email": "",
        "disp": [], "fila_turno": 0
    }, ws

def _save(sess, ws):
    r = sess["row_ws"]
    ws.update_cell(r, 1, sess.get("phone", ""))
    ws.update_cell(r, 2, sess.get("step", "menu"))
    ws.update_cell(r, 3, sess.get("nombre", ""))
    ws.update_cell(r, 4, sess.get("fecha", ""))
    ws.update_cell(r, 5, sess.get("hora", ""))
    ws.update_cell(r, 6, sess.get("email", ""))
    ws.update_cell(r, 7, "|".join(sess.get("disp", [])))
    ws.update_cell(r, 8, str(sess.get("fila_turno", "")))

def _reset(sess, ws):
    r = sess["row_ws"]
    for col in range(2, 9):
        ws.update_cell(r, col, "")
    ws.update_cell(r, 2, "menu")

# ─── Helpers ─────────────────────────────────────────────────────

def _enviar(to, body):
    if not TWILIO_SID:
        log.warning("Twilio no configurado")
        return False
    try:
        r = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json",
            data={"From": TWILIO_WA_FROM, "To": to, "Body": body},
            auth=(TWILIO_SID, TWILIO_TOKEN),
            timeout=10
        )
        if r.status_code != 201:
            log.error(f"Twilio {r.status_code}: {r.text}")
        return r.status_code == 201
    except Exception as e:
        log.error(f"Excepción Twilio: {e}")
        return False

def _ocupados(sheet):
    ocupados = {}
    try:
        rows = sheet.get_all_values()
        if len(rows) < 2:
            return ocupados
        h = rows[0]
        i_f = h.index("Fecha")  if "Fecha"  in h else 3
        i_h = h.index("Hora")   if "Hora"   in h else 4
        i_e = h.index("Estado") if "Estado" in h else 5
        for r in rows[1:]:
            if len(r) <= max(i_f, i_h, i_e):
                continue
            if r[i_e].strip().lower() == "cancelado":
                continue
            f = r[i_f].strip()
            h2 = r[i_h].strip()
            if f:
                ocupados.setdefault(f, set()).add(h2)
    except Exception as e:
        log.error(f"Error leyendo ocupados: {e}")
    return ocupados

def _fechas_disponibles(sheet):
    hoy = datetime.today().date()
    y, m = hoy.year, hoy.month
    _, ult = monthrange(y, m)
    oc = _ocupados(sheet)
    disp = []
    for d in range(hoy.day, ult + 1):
        dt = datetime(y, m, d).date()
        if dt.weekday() >= 5:
            continue
        f = dt.strftime("%d/%m/%Y")
        if len(HORARIOS) - len(oc.get(f, set())) > 0:
            disp.append(f)
    return disp, oc

def _asignar_hora(fecha, oc):
    for h in HORARIOS:
        if h not in oc.get(fecha, set()):
            return h
    return None

def _buscar_turno(sheet, nombre):
    try:
        rows = sheet.get_all_values()
        if len(rows) < 2:
            return None, None
        h = rows[0]
        for i, r in enumerate(rows[1:], 2):
            if len(r) < len(h):
                r += [""] * (len(h) - len(r))
            t = dict(zip(h, r))
            if (nombre.lower() in t.get("Nombre", "").lower()
                    and t.get("Estado", "").lower() != "cancelado"):
                return i, t
    except Exception as e:
        log.error(f"Error buscando turno: {e}")
    return None, None

def _menu_fechas(disp):
    nums = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
    lineas = []
    for i, f in enumerate(disp[:10]):
        em = nums[i] if i < len(nums) else f"{i+1}."
        lineas.append(f"{em} {f}")
    return "📅 *Fechas disponibles:*\n\n" + "\n".join(lineas) + "\n\n_Respondé con el número:_"


# ─── Procesador ──────────────────────────────────────────────────

def procesar(phone, msg, sheet):
    sess, ws = _get_session(phone, sheet)
    txt  = msg.strip()
    low  = txt.lower()
    step = sess["step"]

    log.info(f"WA [{phone}] step={step} msg={txt[:40]}")

    if low in ("0","menu","menú","inicio","hola","buenas","hi","ola"):
        _reset(sess, ws)
        _enviar(phone, MENU)
        return

    # ── MENÚ ────────────────────────────────────────────────────
    if step == "menu":
        if txt == "1":
            sess["step"] = "nuevo_nombre"; _save(sess, ws)
            _enviar(phone, "📝 *Nuevo turno*\n\nIngresá tu *nombre completo*:")
        elif txt == "2":
            sess["step"] = "mod_nombre"; _save(sess, ws)
            _enviar(phone, "🔍 *Modificar turno*\n\nIngresá el nombre con que sacaste el turno:")
        elif txt == "3":
            sess["step"] = "cancel_nombre"; _save(sess, ws)
            _enviar(phone, "❌ *Cancelar turno*\n\nIngresá el nombre con que sacaste el turno:")
        elif txt == "4":
            _enviar(phone, INFO)
        else:
            _enviar(phone, MENU)
        return

    # ── SACAR TURNO ─────────────────────────────────────────────
    if step == "nuevo_nombre":
        sess["nombre"] = txt.title()
        try:
            disp, _ = _fechas_disponibles(sheet)
        except Exception as e:
            log.error(f"Error fechas: {e}")
            _enviar(phone, "❌ Error al consultar la agenda. Intentá de nuevo.")
            return
        if not disp:
            _enviar(phone, "😔 No hay fechas disponibles este mes.\nLlamanos al *(3794) 34-9278*.")
            _reset(sess, ws)
            return
        sess["disp"] = disp
        sess["step"] = "nuevo_fecha"
        _save(sess, ws)
        _enviar(phone, _menu_fechas(disp))
        return

    if step == "nuevo_fecha":
        disp = sess.get("disp", [])
        fecha_elegida = None
        if txt.isdigit():
            idx = int(txt) - 1
            if 0 <= idx < len(disp):
                fecha_elegida = disp[idx]
        if not fecha_elegida:
            _enviar(phone, f"⚠️ Elegí un número del 1 al {min(len(disp),10)}.")
            return
        try:
            _, oc = _fechas_disponibles(sheet)
            hora = _asignar_hora(fecha_elegida, oc)
        except Exception as e:
            log.error(f"Error hora: {e}")
            _enviar(phone, "❌ Error al asignar horario.")
            return
        if not hora:
            _enviar(phone, "😔 Esa fecha se llenó recién. Elegí otra:")
            _enviar(phone, _menu_fechas(disp))
            return
        sess["fecha"] = fecha_elegida
        sess["hora"]  = hora
        sess["step"]  = "nuevo_email"
        _save(sess, ws)
        _enviar(phone, f"✅ *{fecha_elegida}* a las *{hora}hs*\n\nIngresá tu *email* para la confirmación:")
        return

    if step == "nuevo_email":
        nombre = sess.get("nombre", "")
        fecha  = sess.get("fecha", "")
        hora   = sess.get("hora", "")
        email  = txt.strip()
        tel    = re.sub(r"\D", "", phone)
        try:
            sheet.append_row([nombre, tel, email, fecha, hora, "Pendiente"])
            log.info(f"✅ Turno guardado en Sheets: {nombre} {fecha} {hora}")
        except Exception as e:
            log.error(f"❌ Error guardando turno en Sheets: {e}")
            _enviar(phone, "❌ No se pudo guardar el turno. Intentá de nuevo o llamanos al (3794) 34-9278.")
            return
        _enviar(phone,
            f"🎉 *¡Turno solicitado!*\n\n"
            f"👤 {nombre}\n"
            f"📅 {fecha}  ⏰ {hora}hs\n"
            f"📱 {tel}\n"
            f"✉️ {email}\n\n"
            f"Te avisaremos cuando esté *confirmado* por este chat y a tu email.\n\n"
            f"📍 C. Pellegrini 799, Corrientes\n"
            f"📞 (3794) 34-9278\n\n"
            f"Escribí *0* si necesitás algo más 😊"
        )
        _reset(sess, ws)
        return

    # ── MODIFICAR ───────────────────────────────────────────────
    if step == "mod_nombre":
        fila, t = _buscar_turno(sheet, txt)
        if not t:
            _enviar(phone, "🔍 No encontré turno con ese nombre.\nEscribí *0* para volver al menú.")
            _reset(sess, ws)
            return
        sess["fila_turno"] = fila
        try:
            disp, _ = _fechas_disponibles(sheet)
        except Exception as e:
            log.error(f"Error fechas mod: {e}")
            _enviar(phone, "❌ Error al consultar agenda.")
            return
        sess["disp"] = disp
        sess["step"] = "mod_fecha"
        _save(sess, ws)
        _enviar(phone,
            f"📋 Turno encontrado:\n"
            f"👤 {t.get('Nombre','')}\n"
            f"📅 {t.get('Fecha','')}  ⏰ {t.get('Hora','')}\n\n"
            + _menu_fechas(disp)
        )
        return

    if step == "mod_fecha":
        disp = sess.get("disp", [])
        fecha_elegida = None
        if txt.isdigit():
            idx = int(txt) - 1
            if 0 <= idx < len(disp):
                fecha_elegida = disp[idx]
        if not fecha_elegida:
            _enviar(phone, f"⚠️ Elegí un número del 1 al {min(len(disp),10)}.")
            return
        try:
            _, oc = _fechas_disponibles(sheet)
            hora = _asignar_hora(fecha_elegida, oc)
        except Exception as e:
            log.error(f"Error hora mod: {e}")
            _enviar(phone, "❌ Error al asignar horario.")
            return
        fila = sess.get("fila_turno", 0)
        try:
            sheet.update_cell(fila, 4, fecha_elegida)
            sheet.update_cell(fila, 5, hora)
            sheet.update_cell(fila, 6, "Pendiente")
        except Exception as e:
            log.error(f"Error modificando Sheets: {e}")
            _enviar(phone, "❌ Error al modificar el turno.")
            return
        _enviar(phone,
            f"✏️ *Turno modificado!*\n\n"
            f"📅 {fecha_elegida}  ⏰ {hora}hs\n\n"
            f"Te avisaremos la confirmación. Escribí *0* si necesitás algo más."
        )
        _reset(sess, ws)
        return

    # ── CANCELAR ────────────────────────────────────────────────
    if step == "cancel_nombre":
        fila, t = _buscar_turno(sheet, txt)
        if not t:
            _enviar(phone, "🔍 No encontré turno con ese nombre.\nEscribí *0* para volver.")
            _reset(sess, ws)
            return
        sess["fila_turno"] = fila
        sess["step"] = "cancel_conf"
        _save(sess, ws)
        _enviar(phone,
            f"⚠️ *¿Confirmás la cancelación?*\n\n"
            f"👤 {t.get('Nombre','')}\n"
            f"📅 {t.get('Fecha','')}  ⏰ {t.get('Hora','')}\n\n"
            f"Respondé *SI* para cancelar o *NO* para mantenerlo."
        )
        return

    if step == "cancel_conf":
        if low in ("si","sí","s","yes"):
            fila = sess.get("fila_turno", 0)
            try:
                sheet.update_cell(fila, 6, "Cancelado")
            except Exception as e:
                log.error(f"Error cancelando: {e}")
                _enviar(phone, "❌ Error al cancelar. Llamanos al (3794) 34-9278.")
                return
            _enviar(phone, "✅ Turno *cancelado*.\n\nEscribí *1* para sacar uno nuevo o *0* para el menú.")
        else:
            _enviar(phone, "👍 Cancelación abortada. Tu turno sigue activo.\n\nEscribí *0* para el menú.")
        _reset(sess, ws)
        return

    # Step desconocido
    log.warning(f"Step desconocido '{step}' para {phone}")
    _reset(sess, ws)
    _enviar(phone, MENU)
