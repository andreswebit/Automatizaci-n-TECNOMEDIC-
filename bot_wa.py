import re, os, requests, logging
from datetime import datetime
from calendar import monthrange

log = logging.getLogger(__name__)

TWILIO_SID     = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN   = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_WA_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

# Mismo esquema que app.py
HORARIOS = ["08:30", "09:45", "11:00", "16:30", "17:45", "19:00"]
MAX_POR_HORARIO = 2

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
    "🕐 Mañana: 8:30 a 13:00hs\n"
    "🌙 Tarde: 16:30 a 20:30hs\n"
    "📍 C. Pellegrini 799, Corrientes\n"
    "📞 (3794) 34-9278\n\n"
    "Escribí *0* para volver al menú."
)

# ── Sesiones en hoja "Sesiones" del mismo Spreadsheet ────────────

def _ws_sesiones(sheet):
    try:
        return sheet.spreadsheet.worksheet("Sesiones")
    except Exception:
        ws = sheet.spreadsheet.add_worksheet(title="Sesiones", rows=500, cols=10)
        ws.append_row(["Phone","Step","Nombre","Fecha","Hora","Email","Disp","FilaTurno"])
        return ws

def _get_session(phone, sheet):
    ws   = _ws_sesiones(sheet)
    rows = ws.get_all_values()
    for i, row in enumerate(rows):
        if i == 0: continue
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
    return {
        "row_ws": len(ws.get_all_values()), "phone": phone, "step": "menu",
        "nombre": "", "fecha": "", "hora": "", "email": "", "disp": [], "fila_turno": 0
    }, ws

def _save(sess, ws):
    r = sess["row_ws"]
    ws.update_cell(r, 1, sess.get("phone",""))
    ws.update_cell(r, 2, sess.get("step","menu"))
    ws.update_cell(r, 3, sess.get("nombre",""))
    ws.update_cell(r, 4, sess.get("fecha",""))
    ws.update_cell(r, 5, sess.get("hora",""))
    ws.update_cell(r, 6, sess.get("email",""))
    ws.update_cell(r, 7, "|".join(sess.get("disp",[])))
    ws.update_cell(r, 8, str(sess.get("fila_turno","")))

def _reset(sess, ws):
    r = sess["row_ws"]
    for col in range(2, 9): ws.update_cell(r, col, "")
    ws.update_cell(r, 2, "menu")

# ── Helpers ──────────────────────────────────────────────────────

def _enviar(to, body):
    if not TWILIO_SID: return False
    try:
        r = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json",
            data={"From": TWILIO_WA_FROM, "To": to, "Body": body},
            auth=(TWILIO_SID, TWILIO_TOKEN), timeout=10
        )
        if r.status_code != 201: log.error(f"Twilio {r.status_code}: {r.text}")
        return r.status_code == 201
    except Exception as e:
        log.error(f"Excepción Twilio: {e}"); return False

def _get_ocupados(sheet):
    """Retorna {fecha: {hora: cantidad}} para todos los turnos activos."""
    ocupados = {}
    try:
        rows = sheet.get_all_values()
        if len(rows) < 2: return ocupados
        h = rows[0]
        i_f = h.index("Fecha")  if "Fecha"  in h else 3
        i_h = h.index("Hora")   if "Hora"   in h else 4
        i_e = h.index("Estado") if "Estado" in h else 5
        for r in rows[1:]:
            if len(r) <= max(i_f, i_h, i_e): continue
            if r[i_e].strip().lower() == "cancelado": continue
            f = r[i_f].strip(); hh = r[i_h].strip()
            if not f: continue
            ocupados.setdefault(f, {})
            ocupados[f][hh] = ocupados[f].get(hh, 0) + 1
    except Exception as e:
        log.error(f"Error get_ocupados: {e}")
    return ocupados

def _fechas_con_slots(sheet):
    """Retorna lista de fechas con al menos 1 slot libre este mes."""
    hoy = datetime.today().date()
    y, m = hoy.year, hoy.month
    _, ult = monthrange(y, m)
    oc = _get_ocupados(sheet)
    disp = []
    for d in range(hoy.day, ult + 1):
        dt = datetime(y, m, d).date()
        if dt.weekday() >= 5: continue
        f = dt.strftime("%d/%m/%Y")
        conteos = oc.get(f, {})
        libres = sum(1 for h in HORARIOS if conteos.get(h, 0) < MAX_POR_HORARIO)
        if libres > 0:
            disp.append(f)
    return disp, oc

def _slots_para_fecha(fecha, oc):
    """Retorna lista de horarios disponibles para una fecha dada."""
    conteos = oc.get(fecha, {})
    return [h for h in HORARIOS if conteos.get(h, 0) < MAX_POR_HORARIO]

def _menu_fechas(disp):
    nums = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
    lineas = [f"{nums[i] if i<10 else str(i+1)+'.'} {f}" for i, f in enumerate(disp[:10])]
    return "📅 *Fechas disponibles:*\n\n" + "\n".join(lineas) + "\n\n_Respondé con el número:_"

def _menu_horarios(slots):
    nums = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣"]
    lineas = []
    for i, h in enumerate(slots):
        periodo = "☀️" if h <= "12:00" else "🌙"
        lineas.append(f"{nums[i] if i<6 else str(i+1)+'.'} {periodo} {h}hs")
    return "⏰ *Horarios disponibles:*\n\n" + "\n".join(lineas) + "\n\n_Respondé con el número:_"

def _buscar_turno(sheet, nombre):
    try:
        rows = sheet.get_all_values()
        if len(rows) < 2: return None, None
        h = rows[0]
        for i, r in enumerate(rows[1:], 2):
            if len(r) < len(h): r += [""] * (len(h) - len(r))
            t = dict(zip(h, r))
            if nombre.lower() in t.get("Nombre","").lower() and t.get("Estado","").lower() != "cancelado":
                return i, t
    except Exception as e:
        log.error(f"Error buscando: {e}")
    return None, None


# ── Procesador principal ─────────────────────────────────────────

def procesar(phone, msg, sheet):
    sess, ws = _get_session(phone, sheet)
    txt  = msg.strip()
    low  = txt.lower()
    step = sess["step"]
    log.info(f"WA [{phone}] step={step} msg={txt[:40]}")

    if low in ("0","menu","menú","inicio","hola","buenas","hi","ola"):
        _reset(sess, ws); _enviar(phone, MENU); return

    # ── MENÚ ────────────────────────────────────────────────────
    if step == "menu":
        if txt == "1":   sess["step"]="nuevo_nombre"; _save(sess,ws); _enviar(phone,"📝 *Nuevo turno*\n\nIngresá tu *nombre completo*:")
        elif txt == "2": sess["step"]="mod_nombre";   _save(sess,ws); _enviar(phone,"🔍 *Modificar turno*\n\nIngresá el nombre con que sacaste el turno:")
        elif txt == "3": sess["step"]="cancel_nombre";_save(sess,ws); _enviar(phone,"❌ *Cancelar turno*\n\nIngresá el nombre con que sacaste el turno:")
        elif txt == "4": _enviar(phone, INFO)
        else:            _enviar(phone, MENU)
        return

    # ── SACAR TURNO: nombre ──────────────────────────────────────
    if step == "nuevo_nombre":
        sess["nombre"] = txt.title()
        try:
            disp, _ = _fechas_con_slots(sheet)
        except Exception as e:
            log.error(f"Error fechas: {e}"); _enviar(phone,"❌ Error al consultar agenda. Intentá de nuevo."); return
        if not disp:
            _enviar(phone,"😔 No hay fechas disponibles este mes.\nLlamanos al *(3794) 34-9278*.")
            _reset(sess,ws); return
        sess["disp"] = disp; sess["step"] = "nuevo_fecha"; _save(sess,ws)
        _enviar(phone, _menu_fechas(disp))
        return

    # ── SACAR TURNO: fecha ───────────────────────────────────────
    if step == "nuevo_fecha":
        disp = sess.get("disp",[])
        if not txt.isdigit() or not (1 <= int(txt) <= len(disp)):
            _enviar(phone, f"⚠️ Elegí un número del 1 al {min(len(disp),10)}."); return
        fecha_elegida = disp[int(txt)-1]
        try:
            _, oc = _fechas_con_slots(sheet)
            slots = _slots_para_fecha(fecha_elegida, oc)
        except Exception as e:
            log.error(f"Error slots: {e}"); _enviar(phone,"❌ Error al consultar horarios."); return
        if not slots:
            _enviar(phone,"😔 Esa fecha se llenó recién. Elegí otra:"); _enviar(phone,_menu_fechas(disp)); return
        sess["fecha"] = fecha_elegida
        sess["disp"]  = slots     # reutilizar disp para guardar slots del día
        sess["step"]  = "nuevo_hora"
        _save(sess,ws)
        _enviar(phone, f"📅 *{fecha_elegida}*\n\n" + _menu_horarios(slots))
        return

    # ── SACAR TURNO: horario ─────────────────────────────────────
    if step == "nuevo_hora":
        slots = sess.get("disp",[])
        if not txt.isdigit() or not (1 <= int(txt) <= len(slots)):
            _enviar(phone, f"⚠️ Elegí un número del 1 al {len(slots)}."); return
        hora_elegida = slots[int(txt)-1]
        sess["hora"] = hora_elegida; sess["step"] = "nuevo_email"; _save(sess,ws)
        _enviar(phone, f"✅ *{sess['fecha']}* a las *{hora_elegida}hs*\n\nIngresá tu *email* para la confirmación:")
        return

    # ── SACAR TURNO: email → guardar ─────────────────────────────
    if step == "nuevo_email":
        nombre = sess.get("nombre",""); fecha = sess.get("fecha",""); hora = sess.get("hora","")
        email  = txt.strip()
        tel    = re.sub(r"\D","",phone)
        try:
            sheet.append_row([nombre, tel, email, fecha, hora, "Pendiente"])
            log.info(f"✅ Turno WA guardado: {nombre} {fecha} {hora}")
        except Exception as e:
            log.error(f"❌ Error guardando: {e}")
            _enviar(phone,"❌ No se pudo guardar el turno. Intentá de nuevo o llamanos."); return
        _enviar(phone,
            f"🎉 *¡Turno solicitado!*\n\n"
            f"👤 {nombre}\n📅 {fecha}  ⏰ {hora}hs\n"
            f"📱 {tel}\n✉️ {email}\n\n"
            f"Te avisaremos cuando esté *confirmado* por este chat y a tu email.\n\n"
            f"📍 C. Pellegrini 799, Corrientes · 📞 (3794) 34-9278\n\n"
            f"Escribí *0* si necesitás algo más 😊"
        )
        _reset(sess,ws); return

    # ── MODIFICAR: nombre ────────────────────────────────────────
    if step == "mod_nombre":
        fila, t = _buscar_turno(sheet, txt)
        if not t:
            _enviar(phone,"🔍 No encontré turno con ese nombre.\nEscribí *0* para volver.")
            _reset(sess,ws); return
        sess["fila_turno"] = fila
        try:
            disp, _ = _fechas_con_slots(sheet)
        except Exception as e:
            log.error(f"Error fechas mod: {e}"); _enviar(phone,"❌ Error agenda."); return
        sess["disp"] = disp; sess["step"] = "mod_fecha"; _save(sess,ws)
        _enviar(phone,
            f"📋 Turno actual:\n👤 {t.get('Nombre','')}\n📅 {t.get('Fecha','')}  ⏰ {t.get('Hora','')}\n\n"
            + _menu_fechas(disp))
        return

    # ── MODIFICAR: fecha ─────────────────────────────────────────
    if step == "mod_fecha":
        disp = sess.get("disp",[])
        if not txt.isdigit() or not (1 <= int(txt) <= len(disp)):
            _enviar(phone, f"⚠️ Elegí un número del 1 al {min(len(disp),10)}."); return
        fecha_elegida = disp[int(txt)-1]
        try:
            _, oc = _fechas_con_slots(sheet)
            slots = _slots_para_fecha(fecha_elegida, oc)
        except Exception as e:
            log.error(f"Error slots mod: {e}"); _enviar(phone,"❌ Error horarios."); return
        sess["fecha"] = fecha_elegida; sess["disp"] = slots; sess["step"] = "mod_hora"; _save(sess,ws)
        _enviar(phone, f"📅 *{fecha_elegida}*\n\n" + _menu_horarios(slots))
        return

    # ── MODIFICAR: horario → guardar ─────────────────────────────
    if step == "mod_hora":
        slots = sess.get("disp",[])
        if not txt.isdigit() or not (1 <= int(txt) <= len(slots)):
            _enviar(phone, f"⚠️ Elegí un número del 1 al {len(slots)}."); return
        hora_elegida = slots[int(txt)-1]
        fila = sess.get("fila_turno",0)
        try:
            sheet.update_cell(fila,4,sess["fecha"])
            sheet.update_cell(fila,5,hora_elegida)
            sheet.update_cell(fila,6,"Pendiente")
        except Exception as e:
            log.error(f"Error modificando: {e}"); _enviar(phone,"❌ Error al modificar."); return
        _enviar(phone,
            f"✏️ *Turno modificado!*\n\n📅 {sess['fecha']}  ⏰ {hora_elegida}hs\n\n"
            f"Te confirmaremos a la brevedad. Escribí *0* si necesitás algo más.")
        _reset(sess,ws); return

    # ── CANCELAR: nombre ─────────────────────────────────────────
    if step == "cancel_nombre":
        fila, t = _buscar_turno(sheet, txt)
        if not t:
            _enviar(phone,"🔍 No encontré turno con ese nombre.\nEscribí *0* para volver.")
            _reset(sess,ws); return
        sess["fila_turno"] = fila; sess["step"] = "cancel_conf"; _save(sess,ws)
        _enviar(phone,
            f"⚠️ *¿Confirmás la cancelación?*\n\n"
            f"👤 {t.get('Nombre','')}  📅 {t.get('Fecha','')}  ⏰ {t.get('Hora','')}\n\n"
            f"Respondé *SI* para cancelar o *NO* para mantenerlo.")
        return

    # ── CANCELAR: confirmación ────────────────────────────────────
    if step == "cancel_conf":
        if low in ("si","sí","s","yes"):
            try:
                sheet.update_cell(sess.get("fila_turno",0), 6, "Cancelado")
            except Exception as e:
                log.error(f"Error cancelando: {e}"); _enviar(phone,"❌ Error al cancelar."); return
            _enviar(phone,"✅ Turno *cancelado*.\n\nEscribí *1* para sacar uno nuevo o *0* para el menú.")
        else:
            _enviar(phone,"👍 Cancelación abortada. Tu turno sigue activo.\n\nEscribí *0* para el menú.")
        _reset(sess,ws); return

    # Step desconocido
    log.warning(f"Step desconocido '{step}' para {phone}")
    _reset(sess,ws); _enviar(phone,MENU)
