"""
bot_wa.py — Bot WhatsApp TECNOMEDIC
Sesiones en hoja "Sesiones" del mismo Spreadsheet.

Cambios:
- Confirmacion de telefono por 1/2 en vez de SI/NO
- "5" (salir) solo aplica en el menu, no interrumpe flujos activos
- DNI obligatorio
- Opcion consultar turno por DNI
- Emojis y decoracion restaurados
"""

import re, os, requests, logging
from datetime import datetime, date
from calendar import monthrange

log = logging.getLogger(__name__)

TWILIO_SID     = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN   = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_WA_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

HORARIOS        = ["08:30", "09:45", "11:00", "16:30", "17:45", "19:00"]
MAX_POR_HORARIO = 2

IDX_FECHA  = 6
IDX_HORA   = 7
IDX_ESTADO = 8

MENU = (
    "🏥 *TECNOMEDIC* · Cámara Hiperbárica\n\n"
    "1️⃣  Sacar turno\n"
    "2️⃣  Consultar mi turno\n"
    "3️⃣  Modificar turno\n"
    "4️⃣  Cancelar turno\n"
    "5️⃣  Info y horarios\n"
    "6️⃣  Salir\n\n"
    "_Respondé con el número de opción_"
)
INFO = (
    "ℹ️ *TECNOMEDIC*\n\n"
    "🕐 *Mañana:* 8:30 a 13:00hs\n"
    "🌙 *Tarde:*   16:30 a 20:30hs\n\n"
    "📍 C. Pellegrini 799, Corrientes\n"
    "📞 *(3794) 34-9278*\n\n"
    "_Escribí *0* para volver al menú_"
)
DESPEDIDA = (
    "👋 ¡Hasta pronto!\n\n"
    "Cuando necesites escribinos 😊\n"
    "*TECNOMEDIC* · 📞 (3794) 34-9278"
)

OBRAS_SOCIALES = [
    "Particular", "PAMI", "IOSCOR", "OSDE", "Swiss Medical",
    "Galeno", "Medifé", "OSECAC", "OSPAT", "IOMA", "Otra"
]

SALUDOS = {
    "hola","buenas","buenos","hi","hello","ola","buen dia","buen día",
    "buenas tardes","buenas noches","menu","menú","inicio","start",
    "turno","quiero un turno","que tal","como estan","cómo están"
}

# ── Sesiones ──────────────────────────────────────────────────────
# Phone|Step|Nombre|Apellido|DNI|ObraSocial|Telefono|Email|Fecha|Hora|Disp|FilaTurno
#   1     2     3      4      5      6          7      8     9    10   11     12

def _ws_sesiones(sheet):
    HDR = ["Phone","Step","Nombre","Apellido","DNI","ObraSocial",
           "Telefono","Email","Fecha","Hora","Disp","FilaTurno"]
    try:
        ws = sheet.spreadsheet.worksheet("Sesiones")
        if ws.col_count < 12 or ws.row_count < 500:
            ws.resize(rows=max(ws.row_count, 500), cols=max(ws.col_count, 12))
        return ws
    except Exception:
        ws = sheet.spreadsheet.add_worksheet(title="Sesiones", rows=500, cols=12)
        ws.append_row(HDR)
        return ws


def _get_session(phone, sheet):
    ws   = _ws_sesiones(sheet)
    rows = ws.get_all_values()
    for i, row in enumerate(rows):
        if i == 0: continue
        if len(row) > 0 and row[0] == phone:
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
                "disp":       row[10].split("|") if len(row) > 10 and row[10] else [],
                "fila_turno": int(row[11]) if len(row) > 11 and row[11].isdigit() else 0,
            }, ws
    ws.append_row([phone, "menu"] + [""] * 10)
    rows_after = ws.get_all_values()
    return {
        "row_ws": len(rows_after), "phone": phone, "step": "menu",
        "nombre":"", "apellido":"", "dni":"", "obra_social":"",
        "telefono":"", "email":"", "fecha":"", "hora":"",
        "disp":[], "fila_turno":0
    }, ws


def _save(sess, ws):
    r = sess["row_ws"]
    vals = [
        sess.get("phone",""),       sess.get("step","menu"),
        sess.get("nombre",""),      sess.get("apellido",""),
        sess.get("dni",""),         sess.get("obra_social",""),
        sess.get("telefono",""),    sess.get("email",""),
        sess.get("fecha",""),       sess.get("hora",""),
        "|".join(sess.get("disp",[])), str(sess.get("fila_turno",""))
    ]
    try:
        ws.update(f'A{r}:L{r}', [vals])
    except Exception as e:
        log.error(f"Error guardando sesión: {e}")


def _reset(sess, ws):
    sess["step"] = "menu"
    for k in ["nombre","apellido","dni","obra_social","telefono","email","fecha","hora"]:
        sess[k] = ""
    sess["disp"] = []
    sess["fila_turno"] = 0
    _save(sess, ws)


# ── Twilio ────────────────────────────────────────────────────────

def _enviar(to, body):
    if not TWILIO_SID or not TWILIO_TOKEN:
        log.warning("Twilio no configurado")
        return False
    try:
        r = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json",
            data={"From": TWILIO_WA_FROM, "To": to, "Body": body},
            auth=(TWILIO_SID, TWILIO_TOKEN), timeout=10
        )
        ok = r.status_code == 201
        if not ok: log.error(f"Twilio {r.status_code}: {r.text}")
        return ok
    except Exception as e:
        log.error(f"Excepción Twilio: {e}")
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
        log.error(f"Error get_ocupados WA: {e}")
    return ocupados


def _fechas_con_slots(sheet):
    hoy = datetime.today().date()
    y, m = hoy.year, hoy.month
    _, ult = monthrange(y, m)
    oc = _get_ocupados(sheet)
    disp = []
    for d in range(hoy.day, ult + 1):
        dt = date(y, m, d)
        if dt.weekday() >= 5: continue
        f = dt.strftime("%d/%m/%Y")
        libres = sum(1 for h in HORARIOS if oc.get(f,{}).get(h,0) < MAX_POR_HORARIO)
        if libres > 0: disp.append(f)
    return disp, oc


def _slots_para_fecha(fecha, oc):
    return [h for h in HORARIOS if oc.get(fecha,{}).get(h,0) < MAX_POR_HORARIO]


def _menu_fechas(disp):
    nums = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
    lineas = [f"{nums[i] if i < 10 else str(i+1)+'.'} {f}"
              for i, f in enumerate(disp[:10])]
    return "📅 *Fechas disponibles:*\n\n" + "\n".join(lineas) + "\n\n_Respondé con el número:_"


def _menu_horarios(slots):
    nums = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣"]
    lineas = []
    for i, h in enumerate(slots):
        icono = "☀️" if h <= "12:00" else "🌙"
        lineas.append(f"{nums[i] if i < 6 else str(i+1)+'.'} {icono} {h}hs")
    return "⏰ *Horarios disponibles:*\n\n" + "\n".join(lineas) + "\n\n_Respondé con el número:_"


def _menu_obras():
    nums = ["1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟","1️⃣1️⃣"]
    lineas = [f"{nums[i]} {o}" for i, o in enumerate(OBRAS_SOCIALES)]
    return "🏥 *Cobertura médica:*\n\n" + "\n".join(lineas) + "\n\n_Respondé con el número:_"


def _buscar_turno_dni(sheet, dni):
    """Busca turno activo por DNI exacto."""
    dni_limpio = re.sub(r"\D", "", dni)
    try:
        rows = sheet.get_all_values()
        if len(rows) < 2: return None, None
        h = rows[0]
        # DNI está en columna índice 2
        for i, r in enumerate(rows[1:], 2):
            if len(r) <= IDX_ESTADO: continue
            if r[IDX_ESTADO].strip().lower() == "cancelado": continue
            dni_row = re.sub(r"\D", "", r[2] if len(r) > 2 else "")
            if dni_limpio and dni_row == dni_limpio:
                t = {
                    "Nombre":   r[0] if len(r) > 0 else "",
                    "Apellido": r[1] if len(r) > 1 else "",
                    "DNI":      r[2] if len(r) > 2 else "",
                    "ObraSocial": r[3] if len(r) > 3 else "",
                    "Telefono": r[4] if len(r) > 4 else "",
                    "Email":    r[5] if len(r) > 5 else "",
                    "Fecha":    r[IDX_FECHA] if len(r) > IDX_FECHA else "",
                    "Hora":     r[IDX_HORA]  if len(r) > IDX_HORA  else "",
                    "Estado":   r[IDX_ESTADO] if len(r) > IDX_ESTADO else "",
                }
                return i, t
    except Exception as e:
        log.error(f"Error buscando turno por DNI: {e}")
    return None, None


def _tel_desde_phone(phone):
    return re.sub(r"\D", "", phone)


# ══════════════════════════════════════════════════════════════════
# PROCESADOR PRINCIPAL
# ══════════════════════════════════════════════════════════════════

def procesar(phone, msg, sheet):
    sess, ws = _get_session(phone, sheet)
    txt  = msg.strip()
    low  = txt.lower().strip()
    step = sess["step"]
    log.info(f"📱 WA [{phone}] step={step} msg={txt[:50]}")

    # ── Comando universal: "0" siempre vuelve al menú ─────────────
    if txt == "0":
        _reset(sess, ws)
        _enviar(phone, MENU)
        return

    # ── "6" y palabras de salida: solo en el menú o sin flujo activo ──
    # IMPORTANTE: no interrumpir si el usuario está eligiendo una opción numerada
    pasos_con_numeros = {
        "nuevo_obra_social", "nuevo_telefono", "nuevo_fecha", "nuevo_hora",
        "mod_fecha", "mod_hora", "cancel_conf"
    }
    if txt == "6" and step not in pasos_con_numeros:
        _reset(sess, ws)
        _enviar(phone, DESPEDIDA)
        return

    # ── Saludos: solo en menú ──────────────────────────────────────
    if step == "menu":
        if low in SALUDOS or txt not in ("1","2","3","4","5","6"):
            _enviar(phone, MENU)
            return

    # ════════════════════════════════════════════════════════════════
    # MENÚ PRINCIPAL
    # ════════════════════════════════════════════════════════════════
    if step == "menu":
        if txt == "1":
            sess["step"] = "nuevo_nombre"
            _save(sess, ws)
            _enviar(phone, "📝 *Nuevo turno*\n\nIngresá tu *nombre*:")
        elif txt == "2":
            sess["step"] = "consultar_dni"
            _save(sess, ws)
            _enviar(phone, "🔍 *Consultar turno*\n\nIngresá tu *DNI* (solo números):")
        elif txt == "3":
            sess["step"] = "mod_dni"
            _save(sess, ws)
            _enviar(phone, "✏️ *Modificar turno*\n\nIngresá tu *DNI* para encontrar el turno:")
        elif txt == "4":
            sess["step"] = "cancel_dni"
            _save(sess, ws)
            _enviar(phone, "❌ *Cancelar turno*\n\nIngresá tu *DNI* para encontrar el turno:")
        elif txt == "5":
            _enviar(phone, INFO)
        elif txt == "6":
            _reset(sess, ws)
            _enviar(phone, DESPEDIDA)
        return

    # ════════════════════════════════════════════════════════════════
    # CONSULTAR TURNO POR DNI
    # ════════════════════════════════════════════════════════════════
    if step == "consultar_dni":
        dni_limpio = re.sub(r"\D", "", txt)
        if len(dni_limpio) < 7:
            _enviar(phone, "⚠️ DNI inválido. Ingresá solo números (ej: 32456789):")
            return
        fila, t = _buscar_turno_dni(sheet, dni_limpio)
        if not t:
            _enviar(phone,
                "🔍 No encontré ningún turno activo con ese DNI.\n\n"
                "_Escribí *0* para volver al menú._"
            )
            _reset(sess, ws)
            return
        estado_emoji = {"Confirmado":"✅","Pendiente":"⏳","Cancelado":"❌"}.get(t.get("Estado",""), "📋")
        _enviar(phone,
            f"📋 *Tu turno:*\n\n"
            f"👤 {t.get('Nombre','')} {t.get('Apellido','')}\n"
            f"🆔 DNI: {t.get('DNI','')}\n"
            f"🏥 {t.get('ObraSocial','')}\n"
            f"📅 {t.get('Fecha','')}  ⏰ {t.get('Hora','')}hs\n"
            f"{estado_emoji} Estado: *{t.get('Estado','')}*\n\n"
            f"_Escribí *0* para volver al menú_"
        )
        _reset(sess, ws)
        return

    # ════════════════════════════════════════════════════════════════
    # SACAR TURNO
    # ════════════════════════════════════════════════════════════════
    if step == "nuevo_nombre":
        if len(txt) < 2:
            _enviar(phone, "⚠️ Nombre muy corto. Ingresá tu nombre:")
            return
        sess["nombre"] = txt.title()
        sess["step"]   = "nuevo_apellido"
        _save(sess, ws)
        _enviar(phone, f"👤 *{sess['nombre']}*\n\nIngresá tu *apellido*:")
        return

    if step == "nuevo_apellido":
        if len(txt) < 2:
            _enviar(phone, "⚠️ Apellido muy corto. Intentá de nuevo:")
            return
        sess["apellido"] = txt.title()
        sess["step"]     = "nuevo_dni"
        _save(sess, ws)
        _enviar(phone,
            f"👤 {sess['nombre']} *{sess['apellido']}*\n\n"
            f"🆔 ¿Cuál es tu *DNI*? (solo números)"
        )
        return

    if step == "nuevo_dni":
        dni_limpio = re.sub(r"\D", "", txt)
        if len(dni_limpio) < 7:
            _enviar(phone, "⚠️ DNI inválido. Ingresá solo números (ej: 32456789):")
            return
        sess["dni"]  = dni_limpio
        sess["step"] = "nuevo_obra_social"
        _save(sess, ws)
        _enviar(phone, _menu_obras())
        return

    if step == "nuevo_obra_social":
        if not txt.isdigit() or not (1 <= int(txt) <= len(OBRAS_SOCIALES)):
            _enviar(phone, f"⚠️ Elegí un número del 1 al {len(OBRAS_SOCIALES)}.")
            return
        sess["obra_social"] = OBRAS_SOCIALES[int(txt) - 1]
        sess["step"]        = "nuevo_telefono"
        _save(sess, ws)
        tel_wa = _tel_desde_phone(phone)
        _enviar(phone,
            f"🏥 *{sess['obra_social']}*\n\n"
            f"📱 Tu número de WhatsApp es: *+{tel_wa}*\n\n"
            f"1️⃣  Sí, usar ese número\n"
            f"2️⃣  No, ingresar otro\n\n"
            f"_Respondé con 1 o 2:_"
        )
        return

    if step == "nuevo_telefono":
        if txt == "1":
            sess["telefono"] = _tel_desde_phone(phone)
            sess["step"]     = "nuevo_email"
            _save(sess, ws)
            _enviar(phone, "✉️ Ingresá tu *email* para la confirmación del turno:")
        elif txt == "2":
            sess["step"] = "nuevo_telefono_manual"
            _save(sess, ws)
            _enviar(phone, "📱 Ingresá el número de teléfono (solo números, con código de área):")
        else:
            _enviar(phone,
                "⚠️ Respondé con:\n"
                "1️⃣  Sí, usar mi número de WhatsApp\n"
                "2️⃣  No, ingresar otro número"
            )
        return

    if step == "nuevo_telefono_manual":
        tel_limpio = re.sub(r"\D", "", txt)
        if len(tel_limpio) < 8:
            _enviar(phone, "⚠️ Número inválido. Ingresá el teléfono con código de área (ej: 3794123456):")
            return
        sess["telefono"] = tel_limpio
        sess["step"]     = "nuevo_email"
        _save(sess, ws)
        _enviar(phone, "✉️ Ingresá tu *email* para la confirmación del turno:")
        return

    if step == "nuevo_email":
        if "@" not in txt or "." not in txt.split("@")[-1]:
            _enviar(phone, "⚠️ Email inválido. Ejemplo: nombre@mail.com\nIntentá de nuevo:")
            return
        sess["email"] = txt.lower().strip()
        _save(sess, ws)
        try:
            disp, _ = _fechas_con_slots(sheet)
        except Exception as e:
            log.error(f"Error buscando fechas: {e}")
            _enviar(phone, "❌ Error al consultar la agenda. Intentá en unos minutos.")
            return
        if not disp:
            _enviar(phone,
                "😔 No hay fechas disponibles este mes.\n\n"
                "📞 Llamanos al *(3794) 34-9278* para coordinar."
            )
            _reset(sess, ws)
            return
        sess["disp"] = disp
        sess["step"] = "nuevo_fecha"
        _save(sess, ws)
        _enviar(phone, _menu_fechas(disp))
        return

    if step == "nuevo_fecha":
        disp = sess.get("disp", [])
        if not txt.isdigit() or not (1 <= int(txt) <= len(disp)):
            _enviar(phone, f"⚠️ Elegí un número del 1 al {min(len(disp), 10)}.")
            return
        fecha_elegida = disp[int(txt) - 1]
        try:
            _, oc = _fechas_con_slots(sheet)
            slots = _slots_para_fecha(fecha_elegida, oc)
        except Exception as e:
            log.error(f"Error buscando horarios: {e}")
            _enviar(phone, "❌ Error al consultar horarios. Intentá de nuevo.")
            return
        if not slots:
            _enviar(phone, "😔 Esa fecha se llenó. Elegí otra:")
            _enviar(phone, _menu_fechas(disp))
            return
        sess["fecha"] = fecha_elegida
        sess["disp"]  = slots
        sess["step"]  = "nuevo_hora"
        _save(sess, ws)
        _enviar(phone, f"📅 *{fecha_elegida}*\n\n" + _menu_horarios(slots))
        return

    if step == "nuevo_hora":
        slots = sess.get("disp", [])
        if not txt.isdigit() or not (1 <= int(txt) <= len(slots)):
            _enviar(phone, f"⚠️ Elegí un número del 1 al {len(slots)}.")
            return
        hora_elegida = slots[int(txt) - 1]
        try:
            sheet.append_row([
                sess.get("nombre",""),      sess.get("apellido",""),
                sess.get("dni",""),         sess.get("obra_social",""),
                sess.get("telefono",""),    sess.get("email",""),
                sess.get("fecha",""),       hora_elegida, "Pendiente"
            ])
            log.info(f"✅ Turno WA: {sess['nombre']} {sess['apellido']} DNI:{sess['dni']} {sess['fecha']} {hora_elegida}")
        except Exception as e:
            log.error(f"Error guardando turno WA: {e}")
            _enviar(phone, "❌ No se pudo guardar el turno. Llamanos al 📞 (3794) 34-9278.")
            return
        _enviar(phone,
            f"🎉 *¡Turno solicitado!*\n\n"
            f"👤 {sess['nombre']} {sess['apellido']}\n"
            f"🆔 DNI: {sess['dni']}\n"
            f"🏥 {sess['obra_social']}\n"
            f"📱 {sess['telefono']}\n"
            f"✉️  {sess['email']}\n\n"
            f"📅 {sess['fecha']}  ⏰ {hora_elegida}hs\n\n"
            f"⏳ Te avisamos cuando esté *confirmado*.\n\n"
            f"📍 C. Pellegrini 799, Corrientes\n"
            f"📞 (3794) 34-9278\n\n"
            f"_Escribí *0* para el menú o *6* para salir_ 😊"
        )
        _reset(sess, ws)
        return

    # ════════════════════════════════════════════════════════════════
    # MODIFICAR TURNO (por DNI)
    # ════════════════════════════════════════════════════════════════
    if step == "mod_dni":
        dni_limpio = re.sub(r"\D", "", txt)
        if len(dni_limpio) < 7:
            _enviar(phone, "⚠️ DNI inválido. Ingresá solo números:")
            return
        fila, t = _buscar_turno_dni(sheet, dni_limpio)
        if not t:
            _enviar(phone,
                "🔍 No encontré turno activo con ese DNI.\n\n"
                "_Escribí *0* para volver al menú._"
            )
            _reset(sess, ws)
            return
        sess["fila_turno"] = fila
        try:
            disp, _ = _fechas_con_slots(sheet)
        except Exception as e:
            log.error(f"Error fechas modificar: {e}")
            _enviar(phone, "❌ Error consultando agenda.")
            return
        sess["disp"] = disp
        sess["step"] = "mod_fecha"
        _save(sess, ws)
        _enviar(phone,
            f"📋 *Turno actual:*\n\n"
            f"👤 {t.get('Nombre','')} {t.get('Apellido','')}\n"
            f"📅 {t.get('Fecha','')}  ⏰ {t.get('Hora','')}hs\n\n"
            + _menu_fechas(disp)
        )
        return

    if step == "mod_fecha":
        disp = sess.get("disp", [])
        if not txt.isdigit() or not (1 <= int(txt) <= len(disp)):
            _enviar(phone, f"⚠️ Elegí un número del 1 al {min(len(disp), 10)}.")
            return
        fecha_elegida = disp[int(txt) - 1]
        try:
            _, oc = _fechas_con_slots(sheet)
            slots = _slots_para_fecha(fecha_elegida, oc)
        except Exception as e:
            log.error(f"Error horarios modificar: {e}")
            _enviar(phone, "❌ Error consultando horarios.")
            return
        sess["fecha"] = fecha_elegida
        sess["disp"]  = slots
        sess["step"]  = "mod_hora"
        _save(sess, ws)
        _enviar(phone, f"📅 *{fecha_elegida}*\n\n" + _menu_horarios(slots))
        return

    if step == "mod_hora":
        slots = sess.get("disp", [])
        if not txt.isdigit() or not (1 <= int(txt) <= len(slots)):
            _enviar(phone, f"⚠️ Elegí un número del 1 al {len(slots)}.")
            return
        hora_elegida = slots[int(txt) - 1]
        fila = sess.get("fila_turno", 0)
        try:
            sheet.update(f'G{fila}:I{fila}', [[sess["fecha"], hora_elegida, "Pendiente"]])
        except Exception as e:
            log.error(f"Error modificando turno: {e}")
            _enviar(phone, "❌ Error al modificar. Llamanos al 📞 (3794) 34-9278.")
            return
        _enviar(phone,
            f"✏️ *¡Turno modificado!*\n\n"
            f"📅 Nueva fecha: {sess['fecha']}\n"
            f"⏰ Nueva hora:  {hora_elegida}hs\n\n"
            f"⏳ Te confirmaremos a la brevedad.\n\n"
            f"_Escribí *0* para el menú o *6* para salir._"
        )
        _reset(sess, ws)
        return

    # ════════════════════════════════════════════════════════════════
    # CANCELAR TURNO (por DNI)
    # ════════════════════════════════════════════════════════════════
    if step == "cancel_dni":
        dni_limpio = re.sub(r"\D", "", txt)
        if len(dni_limpio) < 7:
            _enviar(phone, "⚠️ DNI inválido. Ingresá solo números:")
            return
        fila, t = _buscar_turno_dni(sheet, dni_limpio)
        if not t:
            _enviar(phone,
                "🔍 No encontré turno activo con ese DNI.\n\n"
                "_Escribí *0* para volver al menú._"
            )
            _reset(sess, ws)
            return
        sess["fila_turno"] = fila
        sess["step"]       = "cancel_conf"
        _save(sess, ws)
        _enviar(phone,
            f"⚠️ *¿Confirmás la cancelación?*\n\n"
            f"👤 {t.get('Nombre','')} {t.get('Apellido','')}\n"
            f"🆔 DNI: {t.get('DNI','')}\n"
            f"📅 {t.get('Fecha','')}  ⏰ {t.get('Hora','')}hs\n\n"
            f"1️⃣  Sí, cancelar el turno\n"
            f"2️⃣  No, mantener el turno\n\n"
            f"_Respondé con 1 o 2:_"
        )
        return

    if step == "cancel_conf":
        if txt == "1":
            fila = sess.get("fila_turno", 0)
            try:
                sheet.update_cell(fila, IDX_ESTADO + 1, "Cancelado")
            except Exception as e:
                log.error(f"Error cancelando: {e}")
                _enviar(phone, "❌ Error al cancelar. Llamanos al 📞 (3794) 34-9278.")
                return
            _enviar(phone,
                "✅ *Turno cancelado.*\n\n"
                "Si necesitás otro turno escribinos o llamá al 📞 (3794) 34-9278.\n\n"
                "_Escribí *0* para el menú._"
            )
        elif txt == "2":
            _enviar(phone,
                "👍 Cancelación abortada. Tu turno sigue activo.\n\n"
                "_Escribí *0* para el menú._"
            )
        else:
            _enviar(phone,
                "⚠️ Respondé con:\n"
                "1️⃣  Sí, cancelar el turno\n"
                "2️⃣  No, mantener el turno"
            )
            return
        _reset(sess, ws)
        return

    # Step desconocido → menú
    log.warning(f"Step desconocido '{step}' para {phone}")
    _reset(sess, ws)
    _enviar(phone, MENU)
