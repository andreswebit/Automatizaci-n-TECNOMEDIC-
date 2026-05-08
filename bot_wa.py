"""
bot_wa.py — Bot WhatsApp TECNOMEDIC
Sesiones persistidas en hoja "Sesiones" del mismo Google Spreadsheet.

Flujo "Sacar turno":
  1. Nombre
  2. Apellido
  3. DNI (opcional — se puede saltar con "no")
  4. Obra Social
  5. Teléfono (pre-cargado del número WA, se puede confirmar o corregir)
  6. Email
  7. Elegir fecha (menú numerado)
  8. Elegir horario (menú numerado)
  → Guardar en Sheets + mensaje de confirmación

n8n: NO se usa — los emails los envía app.py vía SMTP directamente.
"""

import re, os, requests, logging
from datetime import datetime
from calendar import monthrange

log = logging.getLogger(__name__)

TWILIO_SID     = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN   = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_WA_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

# Horarios — deben coincidir exactamente con los de app.py
HORARIOS        = ["08:30", "09:45", "11:00", "16:30", "17:45", "19:00"]
MAX_POR_HORARIO = 2

# Columnas Sheets (0-based, deben coincidir con IDX en app.py)
IDX_FECHA  = 6
IDX_HORA   = 7
IDX_ESTADO = 8

# ── Mensajes fijos ────────────────────────────────────────────────
MENU = (
    "🏥 *TECNOMEDIC* · Cámara Hiperbárica\n\n"
    "1️⃣  Sacar turno\n"
    "2️⃣  Modificar turno\n"
    "3️⃣  Cancelar turno\n"
    "4️⃣  Info y horarios\n"
    "5️⃣  Salir\n\n"
    "_Respondé con el número de opción_"
)
INFO = (
    "ℹ️ *TECNOMEDIC*\n\n"
    "🕐 *Mañana:* 8:30 a 13:00hs\n"
    "🌙 *Tarde:*  16:30 a 20:30hs\n"
    "📍 C. Pellegrini 799, Corrientes\n"
    "📞 (3794) 34-9278\n\n"
    "_Escribí *0* para volver al menú._"
)
DESPEDIDA = (
    "👋 ¡Hasta pronto!\n\n"
    "Cuando necesites escribinos al *+54 9 3794775341 *.\n"
    "*TECNOMEDIC*"
)

# Palabras que siempre muestran el menú
_MENU_WORDS = {
    "0","menu","menú","inicio","volver","start",
    "hola","buenas","buenos","hi","hello","ola",
    "buen dia","buen día","buenas tardes","buenas noches",
    "que tal","cómo están","como estan","turno","quiero un turno"
}
# Palabras que despiden
_SALIR_WORDS = {
    "5","salir","exit","chau","bye","adios","adiós",
    "gracias","ok gracias","listo","no gracias","hasta luego"
}


# ── Sesiones en hoja "Sesiones" ───────────────────────────────────
# Columnas: Phone|Step|Nombre|Apellido|DNI|ObraSocial|Telefono|Email|Fecha|Hora|Disp|FilaTurno

def _ws_sesiones(sheet):
    try:
        return sheet.spreadsheet.worksheet("Sesiones")
    except Exception:
        ws = sheet.spreadsheet.add_worksheet(title="Sesiones", rows=500, cols=12)
        ws.append_row(["Phone","Step","Nombre","Apellido","DNI","ObraSocial",
                        "Telefono","Email","Fecha","Hora","Disp","FilaTurno"])
        return ws

def _get_session(phone, sheet):
    ws   = _ws_sesiones(sheet)
    rows = ws.get_all_values()
    for i, row in enumerate(rows):
        if i == 0: continue
        if len(row) > 0 and row[0] == phone:
            disp_raw = row[9] if len(row) > 9 else ""
            return {
                "row_ws":     i + 1,
                "phone":      phone,
                "step":       row[1]  if len(row) > 1  else "menu",
                "nombre":     row[2]  if len(row) > 2  else "",
                "apellido":   row[3]  if len(row) > 3  else "",
                "dni":        row[4]  if len(row) > 4  else "",
                "obra_social": row[5]  if len(row) > 5  else "",
                "telefono":   row[6]  if len(row) > 6  else "",
                "email":      row[7]  if len(row) > 7  else "",
                "fecha":      row[8]  if len(row) > 8  else "",
                "hora":       row[9]  if len(row) > 9  else "",
                "disp":       disp_raw.split("|") if disp_raw else [],
                "fila_turno": int(row[10]) if len(row) > 10 and row[10].isdigit() else 0,
            }, ws
    ws.append_row([phone, "menu", "", "", "", "", "", "", "", ""])
    return {
        "row_ws": len(ws.get_all_values()), "phone": phone, "step": "menu",
        "nombre": "", "apellido": "", "dni": "", "obra_social": "",
        "telefono": "", "email": "", "fecha": "", "hora": "", "disp": [], "fila_turno": 0
    }, ws

def _save(sess, ws):
    r = sess["row_ws"]
    ws.update_cell(r, 1,  sess.get("phone", ""))
    ws.update_cell(r, 2,  sess.get("step", "menu"))
    ws.update_cell(r, 3,  sess.get("nombre", ""))
    ws.update_cell(r, 4,  sess.get("apellido", ""))
    ws.update_cell(r, 5,  sess.get("dni", ""))
    ws.update_cell(r, 6,  sess.get("obra_social", ""))
    ws.update_cell(r, 7,  sess.get("telefono", ""))
    ws.update_cell(r, 8,  sess.get("email", ""))
    ws.update_cell(r, 9,  sess.get("fecha", ""))
    ws.update_cell(r, 10,  sess.get("hora", ""))
    ws.update_cell(r, 11, "|".join(sess.get("disp", [])))
    ws.update_cell(r, 12, str(sess.get("fila_turno", "")))

def _reset(sess, ws):
    r = sess["row_ws"]
    for col in range(2, 12): ws.update_cell(r, col, "")
    ws.update_cell(r, 2, "menu")


# ── Helpers Twilio ────────────────────────────────────────────────

def _enviar(to, body):
    if not TWILIO_SID or not TWILIO_TOKEN:
        log.warning("⚠️ Twilio no configurado — faltan vars de entorno")
        return False
    try:
        r = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json",
            data={"From": TWILIO_WA_FROM, "To": to, "Body": body},
            auth=(TWILIO_SID, TWILIO_TOKEN), timeout=10
        )
        if r.status_code != 201:
            log.error(f"❌ Twilio {r.status_code}: {r.text}")
        return r.status_code == 201
    except Exception as e:
        log.error(f"❌ Excepción Twilio: {e}")
        return False


# ── Helpers agenda ────────────────────────────────────────────────

def _get_ocupados(sheet):
    """Retorna {fecha: {hora: cantidad}} para turnos activos."""
    ocupados = {}
    try:
        rows = sheet.get_all_values()
        if len(rows) < 2: return ocupados
        for r in rows[1:]:
            if len(r) <= IDX_ESTADO: continue
            if r[IDX_ESTADO].strip().lower() == "cancelado": continue
            f  = r[IDX_FECHA].strip()
            hh = r[IDX_HORA].strip()
            if not f: continue
            ocupados.setdefault(f, {})
            ocupados[f][hh] = ocupados[f].get(hh, 0) + 1
    except Exception as e:
        log.error(f"❌ Error get_ocupados WA: {e}")
    return ocupados

def _fechas_con_slots(sheet):
    """Retorna (lista_fechas, dict_ocupados) con al menos 1 slot libre este mes."""
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
        if libres > 0: disp.append(f)
    return disp, oc

def _slots_para_fecha(fecha, oc):
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

def _buscar_turno(sheet, texto):
    """Busca turno activo por nombre o apellido."""
    try:
        rows = sheet.get_all_values()
        if len(rows) < 2: return None, None
        h = rows[0]
        for i, r in enumerate(rows[1:], 2):
            if len(r) <= IDX_ESTADO: continue
            nombre_completo = f"{r[0]} {r[1]}".strip().lower()
            if texto.lower() in nombre_completo and r[IDX_ESTADO].strip().lower() != "cancelado":
                t = dict(zip(h, r)) if h else {}
                t.setdefault("Nombre",   r[0] if len(r) > 0 else "")
                t.setdefault("Apellido", r[1] if len(r) > 1 else "")
                t.setdefault("Fecha",    r[IDX_FECHA] if len(r) > IDX_FECHA else "")
                t.setdefault("Hora",     r[IDX_HORA]  if len(r) > IDX_HORA  else "")
                return i, t
    except Exception as e:
        log.error(f"❌ Error buscando turno: {e}")
    return None, None

def _tel_desde_phone(phone):
    """Extrae número limpio del formato 'whatsapp:+549...'"""
    return re.sub(r"\D", "", phone)


# ══════════════════════════════════════════════════════════════════
# lista de obras sociales
# ══════════════════════════════════════════════════════════════════

OBRAS_SOCIALES = {
    "1": "Particular",
    "2": "PAMI",
    "3": "IOSCOR",
    "4": "OSDE",
    "5": "Swiss Medical",
    "6": "Galeno",
    "7": "Medifé",
    "8": "OSECAC",
    "9": "OSPAT",
    "10": "IOMA",
    "11": "Otra",
    "12": "N/A"
}


# ══════════════════════════════════════════════════════════════════
# PROCESADOR PRINCIPAL
# ══════════════════════════════════════════════════════════════════

def procesar(phone, msg, sheet):
    sess, ws = _get_session(phone, sheet)
    txt  = msg.strip()
    low  = txt.lower().strip()
    step = sess["step"]

    log.info(f"📱 WA [{phone}] step={step} msg={txt[:50]}")

    # ── Salir ─────────────────────────────────────────────────
    if low in _SALIR_WORDS:
        _reset(sess, ws)
        _enviar(phone, DESPEDIDA)
        return

    # ── Menú / Saludo → siempre responde con menú ────────────
    if low in _MENU_WORDS or step == "menu" and txt not in ("1","2","3","4"):
        _reset(sess, ws)
        _enviar(phone, MENU)
        return

    # ══════════════════════════════════════════════════════════
    # MENÚ PRINCIPAL
    # ══════════════════════════════════════════════════════════
    if step == "menu":
        if txt == "1":
            sess["step"] = "nuevo_nombre"; _save(sess, ws)
            _enviar(phone, "📝 *Nuevo turno*\n\nIngresá tu *nombre*:")
        elif txt == "2":
            sess["step"] = "mod_buscar"; _save(sess, ws)
            _enviar(phone, "🔍 *Modificar turno*\n\nIngresá el nombre con que sacaste el turno:")
        elif txt == "3":
            sess["step"] = "cancel_buscar"; _save(sess, ws)
            _enviar(phone, "❌ *Cancelar turno*\n\nIngresá el nombre con que sacaste el turno:")
        elif txt == "4":
            _enviar(phone, INFO)
        else:
            _enviar(phone, MENU)
        return

    # ══════════════════════════════════════════════════════════
    # SACAR TURNO — recolección de datos paso a paso
    # ══════════════════════════════════════════════════════════

    if step == "nuevo_nombre":
        if len(txt) < 2:
            _enviar(phone, "⚠️ Nombre muy corto. Ingresá tu nombre completo:"); return
        sess["nombre"] = txt.title()
        sess["step"]   = "nuevo_apellido"; _save(sess, ws)
        _enviar(phone, f"👤 Nombre: *{sess['nombre']}*\n\nAhora ingresá tu *apellido*:")
        return

    if step == "nuevo_apellido":
        if len(txt) < 2:
            _enviar(phone, "⚠️ Apellido muy corto. Ingresá tu apellido:"); return
        sess["apellido"] = txt.title()
        sess["step"]     = "nuevo_dni"; _save(sess, ws)
        _enviar(phone,
            f"👤 {sess['nombre']} *{sess['apellido']}*\n\n"
            f"¿Cuál es tu *DNI*? (solo números)\n"
            f"_Si no querés ingresarlo, respondé *no*_"
        )
        return

    if step == "nuevo_dni":
        if low in ("no", "no tengo", "sin dni", "-", "n/a", ""):
            sess["dni"] = ""
        else:
            dni_limpio = re.sub(r"\D", "", txt)
            if len(dni_limpio) < 7:
                _enviar(phone, "⚠️ DNI inválido. Ingresá solo números o respondé *no*:")
                return
            sess["dni"] = dni_limpio

        sess["step"] = "nuevo_obra_social"
        _save(sess, ws)

        menu_os = (
         "🏥 *Seleccioná tu obra social:*\n\n"
            "1️⃣ Particular\n"
            "2️⃣ PAMI\n"
            "3️⃣ IOSCOR\n"
            "4️⃣ OSDE\n"
            "5️⃣ Swiss Medical\n"
            "6️⃣ Galeno\n"
            "7️⃣ Medifé\n"
            "8️⃣ OSECAC\n"
            "9️⃣ OSPAT\n"
            "🔟 IOMA\n"
            "1️⃣1️⃣ Otra\n"
            "1️⃣2️⃣ N/A\n\n"
            "_Respondé con el número_"
        )

        _enviar(phone, menu_os)
        return
    
    if step == "nuevo_obra_social":

        if txt not in OBRAS_SOCIALES:
            _enviar(phone, "⚠️ Elegí una opción válida del menú.")
            return

        obra = OBRAS_SOCIALES[txt]

        if obra in ("Otra", "N/A"):
            sess["obra_social"] = ""
        else:
            sess["obra_social"] = obra

        sess["step"] = "nuevo_telefono"
        _save(sess, ws)

        tel_wa = _tel_desde_phone(phone)

        _enviar(
            phone,
            f"📱 Tu número registrado es *+{tel_wa}*\n\n"
            f"¿Es correcto?\n"
            f"Respondé *sí* para confirmar o ingresá otro número:"
        )
        return
    
    if step == "nuevo_telefono":
        if low in ("si","sí","s","yes","ok","correcto","confirmo"):
            sess["telefono"] = _tel_desde_phone(phone)
        else:
            tel_limpio = re.sub(r"\D", "", txt)
            if len(tel_limpio) < 8:
                _enviar(phone, "⚠️ Número inválido. Ingresá el teléfono o respondé *sí* para usar el actual:"); return
            sess["telefono"] = tel_limpio
        sess["step"] = "nuevo_email"; _save(sess, ws)
        _enviar(phone, "✉️ Ingresá tu *email* para recibir la confirmación:")
        return

    if step == "nuevo_email":
        if "@" not in txt or "." not in txt.split("@")[-1]:
            _enviar(phone, "⚠️ Email inválido. Ingresá un email correcto (ej: nombre@mail.com):"); return
        sess["email"] = txt.lower().strip()
        # Ahora buscar fechas disponibles
        try:
            disp, _ = _fechas_con_slots(sheet)
        except Exception as e:
            log.error(f"❌ Error fechas: {e}")
            _enviar(phone, "❌ Error al consultar agenda. Intentá de nuevo o escribí *0*."); return
        if not disp:
            _enviar(phone, "😔 No hay fechas disponibles este mes.\nLlamanos al *(3794) 34-9278*.")
            _reset(sess, ws); return
        sess["disp"] = disp
        sess["step"] = "nuevo_fecha"; _save(sess, ws)
        _enviar(phone, _menu_fechas(disp))
        return

    if step == "nuevo_fecha":
        disp = sess.get("disp", [])
        if not txt.isdigit() or not (1 <= int(txt) <= len(disp)):
            _enviar(phone, f"⚠️ Elegí un número del 1 al {min(len(disp),10)}."); return
        fecha_elegida = disp[int(txt) - 1]
        try:
            _, oc = _fechas_con_slots(sheet)
            slots = _slots_para_fecha(fecha_elegida, oc)
        except Exception as e:
            log.error(f"❌ Error slots: {e}")
            _enviar(phone, "❌ Error al consultar horarios. Intentá de nuevo."); return
        if not slots:
            _enviar(phone, "😔 Esa fecha se llenó. Elegí otra:")
            _enviar(phone, _menu_fechas(disp)); return
        sess["fecha"] = fecha_elegida
        sess["disp"]  = slots
        sess["step"]  = "nuevo_hora"; _save(sess, ws)
        _enviar(phone, f"📅 *{fecha_elegida}*\n\n" + _menu_horarios(slots))
        return

    if step == "nuevo_hora":
        slots = sess.get("disp", [])
        if not txt.isdigit() or not (1 <= int(txt) <= len(slots)):
            _enviar(phone, f"⚠️ Elegí un número del 1 al {len(slots)}."); return
        hora_elegida = slots[int(txt) - 1]

        # Guardar en Sheets
        # Nombre|Apellido|DNI|ObraSocial|Telefono|Email|Fecha|Hora|Estado
        try:
            sheet.append_row([
                sess.get("nombre", ""),
                sess.get("apellido", ""),
                sess.get("dni", ""),
                sess.get("obra_social", ""),
                sess.get("telefono", ""),
                sess.get("email", ""),
                sess.get("fecha", ""),
                hora_elegida,
                "Pendiente"
            ])
            log.info(f"✅ Turno WA: {sess['nombre']} {sess['apellido']} {sess['fecha']} {hora_elegida}")
        except Exception as e:
            log.error(f"❌ Error guardando turno WA: {e}")
            _enviar(phone, "❌ No se pudo guardar el turno. Intentá de nuevo o llamanos al (3794) 34-9278."); return

        _enviar(phone,
            f"🎉 *¡Turno solicitado!*\n\n"
            f"👤 {sess['nombre']} {sess['apellido']}\n"
            f"📱 {sess['telefono']}\n"
            f"✉️  {sess['email']}\n"
            f"📅 {sess['fecha']}  ⏰ {hora_elegida}hs\n\n"
            f"Te avisaremos cuando esté *confirmado* por este chat y a tu email.\n\n"
            f"📍 C. Pellegrini 799, Corrientes\n"
            f"📞 (3794) 34-9278\n\n"
            f"_Escribí *5* para salir o *0* para el menú_ 😊"
        )
        _reset(sess, ws)
        return

    # ══════════════════════════════════════════════════════════
    # MODIFICAR
    # ══════════════════════════════════════════════════════════

    if step == "mod_buscar":
        fila, t = _buscar_turno(sheet, txt)
        if not t:
            _enviar(phone, "🔍 No encontré turno con ese nombre.\nEscribí *0* para volver.")
            _reset(sess, ws); return
        sess["fila_turno"] = fila
        try:
            disp, _ = _fechas_con_slots(sheet)
        except Exception as e:
            log.error(f"❌ Error fechas mod: {e}")
            _enviar(phone, "❌ Error al consultar agenda."); return
        sess["disp"] = disp
        sess["step"] = "mod_fecha"; _save(sess, ws)
        _enviar(phone,
            f"📋 *Turno actual:*\n"
            f"👤 {t.get('Nombre','')} {t.get('Apellido','')}\n"
            f"📅 {t.get('Fecha','')}  ⏰ {t.get('Hora','')}\n\n"
            + _menu_fechas(disp)
        )
        return

    if step == "mod_fecha":
        disp = sess.get("disp", [])
        if not txt.isdigit() or not (1 <= int(txt) <= len(disp)):
            _enviar(phone, f"⚠️ Elegí un número del 1 al {min(len(disp),10)}."); return
        fecha_elegida = disp[int(txt) - 1]
        try:
            _, oc = _fechas_con_slots(sheet)
            slots = _slots_para_fecha(fecha_elegida, oc)
        except Exception as e:
            log.error(f"❌ Error slots mod: {e}")
            _enviar(phone, "❌ Error al consultar horarios."); return
        sess["fecha"] = fecha_elegida
        sess["disp"]  = slots
        sess["step"]  = "mod_hora"; _save(sess, ws)
        _enviar(phone, f"📅 *{fecha_elegida}*\n\n" + _menu_horarios(slots))
        return

    if step == "mod_hora":
        slots = sess.get("disp", [])
        if not txt.isdigit() or not (1 <= int(txt) <= len(slots)):
            _enviar(phone, f"⚠️ Elegí un número del 1 al {len(slots)}."); return
        hora_elegida = slots[int(txt) - 1]
        fila = sess.get("fila_turno", 0)
        try:
            sheet.update_cell(fila, IDX_FECHA  + 1, sess["fecha"])
            sheet.update_cell(fila, IDX_HORA   + 1, hora_elegida)
            sheet.update_cell(fila, IDX_ESTADO + 1, "Pendiente")
        except Exception as e:
            log.error(f"❌ Error modificando Sheets: {e}")
            _enviar(phone, "❌ Error al modificar el turno."); return
        _enviar(phone,
            f"✏️ *Turno modificado!*\n\n"
            f"📅 {sess['fecha']}  ⏰ {hora_elegida}hs\n\n"
            f"Te confirmaremos a la brevedad.\n"
            f"_Escribí *5* para salir o *0* para el menú._"
        )
        _reset(sess, ws); return

    # ══════════════════════════════════════════════════════════
    # CANCELAR
    # ══════════════════════════════════════════════════════════

    if step == "cancel_buscar":
        fila, t = _buscar_turno(sheet, txt)
        if not t:
            _enviar(phone, "🔍 No encontré turno con ese nombre.\nEscribí *0* para volver.")
            _reset(sess, ws); return
        sess["fila_turno"] = fila
        sess["step"]       = "cancel_conf"; _save(sess, ws)
        _enviar(phone,
            f"⚠️ *¿Confirmás la cancelación?*\n\n"
            f"👤 {t.get('Nombre','')} {t.get('Apellido','')}\n"
            f"📅 {t.get('Fecha','')}  ⏰ {t.get('Hora','')}\n\n"
            f"Respondé *SI* para cancelar o *NO* para mantenerlo."
        )
        return

    if step == "cancel_conf":
        if low in ("si","sí","s","yes"):
            fila = sess.get("fila_turno", 0)
            try:
                sheet.update_cell(fila, IDX_ESTADO + 1, "Cancelado")
            except Exception as e:
                log.error(f"❌ Error cancelando: {e}")
                _enviar(phone, "❌ Error al cancelar. Llamanos al (3794) 34-9278."); return
            _enviar(phone,
                "✅ Turno *cancelado*.\n\n"
                "_Escribí *1* si querés sacar un nuevo turno o *5* para salir._"
            )
        else:
            _enviar(phone,
                "👍 Cancelación abortada. Tu turno sigue activo.\n\n"
                "_Escribí *0* para el menú o *5* para salir._"
            )
        _reset(sess, ws); return

    # ── Step desconocido ──────────────────────────────────────
    log.warning(f"⚠️ Step desconocido '{step}' para {phone}")
    _reset(sess, ws)
    _enviar(phone, MENU)
