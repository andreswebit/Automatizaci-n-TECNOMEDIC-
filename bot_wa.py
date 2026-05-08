"""
bot_wa.py — Bot WhatsApp TECNOMEDIC
Sesiones en hoja "Sesiones" del mismo Spreadsheet.

Flujo "Sacar turno":
  1. Nombre  2. Apellido  3. DNI (opcional)  4. Obra Social
  5. Teléfono (pre-cargado del WA)  6. Email
  7. Fecha (menú numerado)  8. Hora (menú numerado)
  → Guarda en Sheets

n8n: NO se usa. Emails van por SMTP en app.py.
"""

import re, os, requests, logging
from datetime import datetime
from calendar import monthrange

log = logging.getLogger(__name__)

TWILIO_SID     = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN   = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_WA_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

HORARIOS        = ["08:30", "09:45", "11:00", "16:30", "17:45", "19:00"]
MAX_POR_HORARIO = 2

# Índices 0-based de la hoja principal (deben coincidir con IDX en app.py)
# Nombre(0)|Apellido(1)|DNI(2)|ObraSocial(3)|Telefono(4)|Email(5)|Fecha(6)|Hora(7)|Estado(8)
IDX_FECHA  = 6
IDX_HORA   = 7
IDX_ESTADO = 8

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
    "Cuando necesites escribinos.\n"
    "*TECNOMEDIC* · (3794) 34-9278"
)

_MENU_WORDS = {
    "0","menu","menú","inicio","volver","start",
    "hola","buenas","buenos","hi","hello","ola",
    "buen dia","buen día","buenas tardes","buenas noches",
    "que tal","como estan","cómo están","turno","quiero un turno"
}
_SALIR_WORDS = {
    "5","salir","exit","chau","bye","adios","adiós",
    "gracias","ok gracias","listo","no gracias","hasta luego"
}

OBRAS_SOCIALES = [
    "Particular","PAMI","IOSCOR","OSDE","Swiss Medical",
    "Galeno","Medifé","OSECAC","OSPAT","IOMA","Otra"
]

# ── Sesiones: 12 columnas ─────────────────────────────────────────
# Phone|Step|Nombre|Apellido|DNI|ObraSocial|Telefono|Email|Fecha|Hora|Disp|FilaTurno
#   1     2     3      4      5      6          7      8     9    10   11     12

def _ws_sesiones(sheet):
    """Obtiene o crea la hoja Sesiones con 12 columnas y 500 filas."""
    HDR  = ["Phone","Step","Nombre","Apellido","DNI","ObraSocial",
            "Telefono","Email","Fecha","Hora","Disp","FilaTurno"]
    ROWS = 500
    COLS = 12
    try:
        ws = sheet.spreadsheet.worksheet("Sesiones")
        # Expandir si fue creada con menos columnas (el error anterior)
        if ws.col_count < COLS or ws.row_count < ROWS:
            ws.resize(rows=max(ws.row_count, ROWS), cols=max(ws.col_count, COLS))
            log.info("✅ Hoja Sesiones redimensionada")
        return ws
    except Exception:
        try:
            ws = sheet.spreadsheet.add_worksheet(title="Sesiones", rows=ROWS, cols=COLS)
            ws.append_row(HDR)
            log.info("✅ Hoja Sesiones creada")
            return ws
        except Exception as e:
            log.error(f"❌ No se pudo crear hoja Sesiones: {e}")
            raise


def _get_session(phone, sheet):
    ws   = _ws_sesiones(sheet)
    rows = ws.get_all_values()
    for i, row in enumerate(rows):
        if i == 0: continue
        if len(row) > 0 and row[0] == phone:
            # col 11 (index 10) = Disp   col 12 (index 11) = FilaTurno
            disp_raw    = row[10] if len(row) > 10 else ""
            fila_turno  = row[11] if len(row) > 11 else ""
            return {
                "row_ws":     i + 1,
                "phone":      phone,
                "step":       row[1]  if len(row) > 1  else "menu",
                "nombre":     row[2]  if len(row) > 2  else "",
                "apellido":   row[3]  if len(row) > 3  else "",
                "dni":        row[4]  if len(row) > 4  else "",
                "obra_social":row[5]  if len(row) > 5  else "",
                "telefono":   row[6]  if len(row) > 6  else "",
                "email":      row[7]  if len(row) > 7  else "",
                "fecha":      row[8]  if len(row) > 8  else "",
                "hora":       row[9]  if len(row) > 9  else "",
                "disp":       disp_raw.split("|") if disp_raw else [],
                "fila_turno": int(fila_turno) if fila_turno.isdigit() else 0,
            }, ws
    ws.append_row([phone, "menu", "", "", "", "", "", "", "", "", "", ""])
    return {
        "row_ws": len(ws.get_all_values()), "phone": phone, "step": "menu",
        "nombre":"", "apellido":"", "dni":"", "obra_social":"",
        "telefono":"", "email":"", "fecha":"", "hora":"",
        "disp":[], "fila_turno":0
    }, ws


def _save(sess, ws):
    r = sess["row_ws"]
    vals = [
        sess.get("phone",""),
        sess.get("step","menu"),
        sess.get("nombre",""),
        sess.get("apellido",""),
        sess.get("dni",""),
        sess.get("obra_social",""),
        sess.get("telefono",""),
        sess.get("email",""),
        sess.get("fecha",""),
        sess.get("hora",""),
        "|".join(sess.get("disp",[])),
        str(sess.get("fila_turno",""))
    ]
    for col_idx, val in enumerate(vals, start=1):
        try:
            ws.update_cell(r, col_idx, val)
        except Exception as e:
            log.error(f"❌ Error guardando sesión col {col_idx}: {e}")


def _reset(sess, ws):
    r = sess["row_ws"]
    try:
        for col in range(2, 13):
            ws.update_cell(r, col, "")
        ws.update_cell(r, 2, "menu")
    except Exception as e:
        log.error(f"❌ Error reseteando sesión: {e}")


# ── Twilio ────────────────────────────────────────────────────────

def _enviar(to, body):
    if not TWILIO_SID or not TWILIO_TOKEN:
        log.warning("⚠️ Twilio no configurado")
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


# ── Agenda ────────────────────────────────────────────────────────

def _get_ocupados(sheet):
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
    hoy = datetime.today().date()
    y, m = hoy.year, hoy.month
    _, ult = monthrange(y, m)
    oc = _get_ocupados(sheet)
    disp = []
    for d in range(hoy.day, ult + 1):
        dt = datetime(y, m, d).date()
        if dt.weekday() >= 5: continue
        f = dt.strftime("%d/%m/%Y")
        libres = sum(1 for h in HORARIOS if oc.get(f,{}).get(h,0) < MAX_POR_HORARIO)
        if libres > 0: disp.append(f)
    return disp, oc


def _slots_para_fecha(fecha, oc):
    return [h for h in HORARIOS if oc.get(fecha,{}).get(h,0) < MAX_POR_HORARIO]


def _menu_fechas(disp):
    nums = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
    lineas = [f"{nums[i] if i<10 else str(i+1)+'.'} {f}" for i,f in enumerate(disp[:10])]
    return "📅 *Fechas disponibles:*\n\n" + "\n".join(lineas) + "\n\n_Respondé con el número:_"


def _menu_horarios(slots):
    nums = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣"]
    lineas = []
    for i, h in enumerate(slots):
        p = "☀️" if h <= "12:00" else "🌙"
        lineas.append(f"{nums[i] if i<6 else str(i+1)+'.'} {p} {h}hs")
    return "⏰ *Horarios disponibles:*\n\n" + "\n".join(lineas) + "\n\n_Respondé con el número:_"


def _menu_obras():
    nums = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟","1️⃣1️⃣"]
    lineas = [f"{nums[i]} {o}" for i,o in enumerate(OBRAS_SOCIALES)]
    return "🏥 *Cobertura médica:*\n\n" + "\n".join(lineas) + "\n\n_Respondé con el número:_"


def _buscar_turno(sheet, texto):
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
    return re.sub(r"\D", "", phone)


# ══════════════════════════════════════════════════════════════════
# PROCESADOR
# ══════════════════════════════════════════════════════════════════

def procesar(phone, msg, sheet):
    sess, ws = _get_session(phone, sheet)
    txt  = msg.strip()
    low  = txt.lower().strip()
    step = sess["step"]
    log.info(f"📱 WA [{phone}] step={step} msg={txt[:50]}")

    # Salir
    if low in _SALIR_WORDS:
        _reset(sess, ws); _enviar(phone, DESPEDIDA); return

    # Menú / saludo
    if low in _MENU_WORDS or (step == "menu" and txt not in ("1","2","3","4")):
        _reset(sess, ws); _enviar(phone, MENU); return

    # ── MENÚ ─────────────────────────────────────────────────
    if step == "menu":
        if txt == "1":   sess["step"]="nuevo_nombre";  _save(sess,ws); _enviar(phone,"📝 *Nuevo turno*\n\nIngresá tu *nombre*:")
        elif txt == "2": sess["step"]="mod_buscar";    _save(sess,ws); _enviar(phone,"🔍 *Modificar turno*\n\nIngresá el nombre con que sacaste el turno:")
        elif txt == "3": sess["step"]="cancel_buscar"; _save(sess,ws); _enviar(phone,"❌ *Cancelar turno*\n\nIngresá el nombre con que sacaste el turno:")
        elif txt == "4": _enviar(phone, INFO)
        else:            _enviar(phone, MENU)
        return

    # ── SACAR TURNO ───────────────────────────────────────────
    if step == "nuevo_nombre":
        if len(txt) < 2: _enviar(phone,"⚠️ Nombre muy corto:"); return
        sess["nombre"] = txt.title(); sess["step"] = "nuevo_apellido"; _save(sess,ws)
        _enviar(phone, f"👤 *{sess['nombre']}*\n\nIngresá tu *apellido*:"); return

    if step == "nuevo_apellido":
        if len(txt) < 2: _enviar(phone,"⚠️ Apellido muy corto:"); return
        sess["apellido"] = txt.title(); sess["step"] = "nuevo_dni"; _save(sess,ws)
        _enviar(phone,
            f"👤 {sess['nombre']} *{sess['apellido']}*\n\n"
            f"¿Cuál es tu *DNI*? (solo números)\n"
            f"_Respondé *no* para saltearlo_"
        ); return

    if step == "nuevo_dni":
        if low in ("no","no tengo","-","n/a",""):
            sess["dni"] = ""
        else:
            dni_limpio = re.sub(r"\D","",txt)
            if len(dni_limpio) < 7: _enviar(phone,"⚠️ DNI inválido. Solo números o respondé *no*:"); return
            sess["dni"] = dni_limpio
        sess["step"] = "nuevo_obra_social"; _save(sess,ws)
        _enviar(phone, _menu_obras()); return

    if step == "nuevo_obra_social":
        if txt.isdigit() and 1 <= int(txt) <= len(OBRAS_SOCIALES):
            sess["obra_social"] = OBRAS_SOCIALES[int(txt)-1]
        else:
            _enviar(phone, f"⚠️ Elegí un número del 1 al {len(OBRAS_SOCIALES)}."); return
        sess["step"] = "nuevo_telefono"; _save(sess,ws)
        tel_wa = _tel_desde_phone(phone)
        _enviar(phone,
            f"🏥 *{sess['obra_social']}*\n\n"
            f"📱 Tu número registrado es *+{tel_wa}*\n\n"
            f"Respondé *sí* para confirmar o ingresá otro número:"
        ); return

    if step == "nuevo_telefono":
        if low in ("si","sí","s","yes","ok","correcto","confirmo"):
            sess["telefono"] = _tel_desde_phone(phone)
        else:
            tel_limpio = re.sub(r"\D","",txt)
            if len(tel_limpio) < 8: _enviar(phone,"⚠️ Número inválido. Ingresá el teléfono o respondé *sí*:"); return
            sess["telefono"] = tel_limpio
        sess["step"] = "nuevo_email"; _save(sess,ws)
        _enviar(phone,"✉️ Ingresá tu *email* para la confirmación:"); return

    if step == "nuevo_email":
        if "@" not in txt or "." not in txt.split("@")[-1]:
            _enviar(phone,"⚠️ Email inválido (ej: nombre@mail.com):"); return
        sess["email"] = txt.lower().strip()
        try:
            disp, _ = _fechas_con_slots(sheet)
        except Exception as e:
            log.error(f"❌ Error fechas: {e}"); _enviar(phone,"❌ Error agenda. Intentá de nuevo."); return
        if not disp:
            _enviar(phone,"😔 No hay fechas disponibles.\nLlamanos al *(3794) 34-9278*.")
            _reset(sess,ws); return
        sess["disp"] = disp; sess["step"] = "nuevo_fecha"; _save(sess,ws)
        _enviar(phone, _menu_fechas(disp)); return

    if step == "nuevo_fecha":
        disp = sess.get("disp",[])
        if not txt.isdigit() or not (1 <= int(txt) <= len(disp)):
            _enviar(phone, f"⚠️ Elegí un número del 1 al {min(len(disp),10)}."); return
        fecha_elegida = disp[int(txt)-1]
        try:
            _, oc = _fechas_con_slots(sheet)
            slots = _slots_para_fecha(fecha_elegida, oc)
        except Exception as e:
            log.error(f"❌ Error slots: {e}"); _enviar(phone,"❌ Error horarios."); return
        if not slots:
            _enviar(phone,"😔 Esa fecha se llenó. Elegí otra:"); _enviar(phone,_menu_fechas(disp)); return
        sess["fecha"] = fecha_elegida; sess["disp"] = slots; sess["step"] = "nuevo_hora"; _save(sess,ws)
        _enviar(phone, f"📅 *{fecha_elegida}*\n\n" + _menu_horarios(slots)); return

    if step == "nuevo_hora":
        slots = sess.get("disp",[])
        if not txt.isdigit() or not (1 <= int(txt) <= len(slots)):
            _enviar(phone, f"⚠️ Elegí un número del 1 al {len(slots)}."); return
        hora_elegida = slots[int(txt)-1]
        try:
            # Nombre|Apellido|DNI|ObraSocial|Telefono|Email|Fecha|Hora|Estado
            sheet.append_row([
                sess.get("nombre",""), sess.get("apellido",""), sess.get("dni",""),
                sess.get("obra_social",""), sess.get("telefono",""), sess.get("email",""),
                sess.get("fecha",""), hora_elegida, "Pendiente"
            ])
            log.info(f"✅ Turno WA: {sess['nombre']} {sess['apellido']} {sess['fecha']} {hora_elegida}")
        except Exception as e:
            log.error(f"❌ Error guardando turno WA: {e}")
            _enviar(phone,"❌ No se pudo guardar. Llamanos al (3794) 34-9278."); return
        _enviar(phone,
            f"🎉 *¡Turno solicitado!*\n\n"
            f"👤 {sess['nombre']} {sess['apellido']}\n"
            f"🏥 {sess['obra_social']}\n"
            f"📱 {sess['telefono']}\n"
            f"✉️  {sess['email']}\n"
            f"📅 {sess['fecha']}  ⏰ {hora_elegida}hs\n\n"
            f"Te avisaremos cuando esté *confirmado* por este chat y a tu email.\n\n"
            f"📍 C. Pellegrini 799, Corrientes · 📞 (3794) 34-9278\n\n"
            f"_Escribí *5* para salir o *0* para el menú_ 😊"
        )
        _reset(sess,ws); return

    # ── MODIFICAR ─────────────────────────────────────────────
    if step == "mod_buscar":
        fila, t = _buscar_turno(sheet, txt)
        if not t:
            _enviar(phone,"🔍 No encontré turno con ese nombre.\nEscribí *0* para volver.")
            _reset(sess,ws); return
        sess["fila_turno"] = fila
        try:
            disp, _ = _fechas_con_slots(sheet)
        except Exception as e:
            log.error(f"❌ Error fechas mod: {e}"); _enviar(phone,"❌ Error agenda."); return
        sess["disp"] = disp; sess["step"] = "mod_fecha"; _save(sess,ws)
        _enviar(phone,
            f"📋 *Turno actual:*\n"
            f"👤 {t.get('Nombre','')} {t.get('Apellido','')}\n"
            f"📅 {t.get('Fecha','')}  ⏰ {t.get('Hora','')}\n\n"
            + _menu_fechas(disp)); return

    if step == "mod_fecha":
        disp = sess.get("disp",[])
        if not txt.isdigit() or not (1 <= int(txt) <= len(disp)):
            _enviar(phone, f"⚠️ Elegí un número del 1 al {min(len(disp),10)}."); return
        fecha_elegida = disp[int(txt)-1]
        try:
            _, oc = _fechas_con_slots(sheet)
            slots = _slots_para_fecha(fecha_elegida, oc)
        except Exception as e:
            log.error(f"❌ Error slots mod: {e}"); _enviar(phone,"❌ Error horarios."); return
        sess["fecha"] = fecha_elegida; sess["disp"] = slots; sess["step"] = "mod_hora"; _save(sess,ws)
        _enviar(phone, f"📅 *{fecha_elegida}*\n\n" + _menu_horarios(slots)); return

    if step == "mod_hora":
        slots = sess.get("disp",[])
        if not txt.isdigit() or not (1 <= int(txt) <= len(slots)):
            _enviar(phone, f"⚠️ Elegí un número del 1 al {len(slots)}."); return
        hora_elegida = slots[int(txt)-1]
        fila = sess.get("fila_turno",0)
        try:
            sheet.update_cell(fila, IDX_FECHA  + 1, sess["fecha"])
            sheet.update_cell(fila, IDX_HORA   + 1, hora_elegida)
            sheet.update_cell(fila, IDX_ESTADO + 1, "Pendiente")
        except Exception as e:
            log.error(f"❌ Error modificando: {e}"); _enviar(phone,"❌ Error al modificar."); return
        _enviar(phone,
            f"✏️ *Turno modificado!*\n\n📅 {sess['fecha']}  ⏰ {hora_elegida}hs\n\n"
            f"Te confirmaremos a la brevedad.\n_Escribí *5* para salir o *0* para el menú._"
        )
        _reset(sess,ws); return

    # ── CANCELAR ──────────────────────────────────────────────
    if step == "cancel_buscar":
        fila, t = _buscar_turno(sheet, txt)
        if not t:
            _enviar(phone,"🔍 No encontré turno con ese nombre.\nEscribí *0* para volver.")
            _reset(sess,ws); return
        sess["fila_turno"] = fila; sess["step"] = "cancel_conf"; _save(sess,ws)
        _enviar(phone,
            f"⚠️ *¿Confirmás la cancelación?*\n\n"
            f"👤 {t.get('Nombre','')} {t.get('Apellido','')}\n"
            f"📅 {t.get('Fecha','')}  ⏰ {t.get('Hora','')}\n\n"
            f"Respondé *SI* o *NO*."
        ); return

    if step == "cancel_conf":
        if low in ("si","sí","s","yes"):
            fila = sess.get("fila_turno",0)
            try:
                sheet.update_cell(fila, IDX_ESTADO + 1, "Cancelado")
            except Exception as e:
                log.error(f"❌ Error cancelando: {e}"); _enviar(phone,"❌ Error al cancelar."); return
            _enviar(phone,"✅ Turno *cancelado*.\n_Escribí *1* para nuevo turno o *5* para salir._")
        else:
            _enviar(phone,"👍 Cancelación abortada. Tu turno sigue activo.\n_Escribí *0* para el menú._")
        _reset(sess,ws); return

    log.warning(f"⚠️ Step desconocido '{step}' para {phone}")
    _reset(sess,ws); _enviar(phone, MENU)
