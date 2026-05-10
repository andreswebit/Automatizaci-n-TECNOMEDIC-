"""
bot_wa.py — Bot WhatsApp TECNOMEDIC
Sesiones en hoja "Sesiones" del mismo Spreadsheet.
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

IDX_FECHA  = 6
IDX_HORA   = 7
IDX_ESTADO = 8

MENU = (
    "TECNOMEDIC - Camara Hiperbarica\n\n"
    "1 - Sacar turno\n"
    "2 - Ver / Modificar turno\n"
    "3 - Cancelar turno\n"
    "4 - Info y horarios\n"
    "5 - Salir\n\n"
    "Responde con el numero de opcion."
)
INFO = (
    "TECNOMEDIC\n\n"
    "Horarios:\n"
    "Manana: 8:30 a 13:00hs\n"
    "Tarde:  16:30 a 20:30hs\n\n"
    "Direccion: C. Pellegrini 799, Corrientes\n"
    "Tel: (3794) 34-9278\n\n"
    "Escribe 0 para volver al menu."
)
DESPEDIDA = (
    "Hasta pronto!\n\n"
    "Cuando necesites escribinos.\n"
    "TECNOMEDIC - (3794) 34-9278"
)

OBRAS_SOCIALES = [
    "Particular", "PAMI", "IOSCOR", "OSDE", "Swiss Medical",
    "Galeno", "Medife", "OSECAC", "OSPAT", "IOMA", "Otra"
]

# ── Sesiones ──────────────────────────────────────────────────────
# Phone|Step|Nombre|Apellido|DNI|ObraSocial|Telefono|Email|Fecha|Hora|Disp|FilaTurno
#   1     2     3      4      5      6          7      8     9    10   11     12

def _ws_sesiones(sheet):
    HDR  = ["Phone","Step","Nombre","Apellido","DNI","ObraSocial",
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
    try:
        ws.update(f'A{r}:L{r}', [vals])
    except Exception as e:
        log.error(f"Error guardando sesion: {e}")


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
        log.error(f"Excepcion Twilio: {e}")
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
        from datetime import date
        dt = date(y, m, d)
        if dt.weekday() >= 5: continue
        f = dt.strftime("%d/%m/%Y")
        libres = sum(1 for h in HORARIOS if oc.get(f,{}).get(h,0) < MAX_POR_HORARIO)
        if libres > 0: disp.append(f)
    return disp, oc


def _slots_para_fecha(fecha, oc):
    return [h for h in HORARIOS if oc.get(fecha,{}).get(h,0) < MAX_POR_HORARIO]


def _menu_fechas(disp):
    lineas = [f"{i+1}. {f}" for i, f in enumerate(disp[:10])]
    return "Fechas disponibles:\n\n" + "\n".join(lineas) + "\n\nResponde con el numero:"


def _menu_horarios(slots):
    lineas = []
    for i, h in enumerate(slots):
        turno = "Manana" if h <= "12:00" else "Tarde"
        lineas.append(f"{i+1}. {turno} {h}hs")
    return "Horarios disponibles:\n\n" + "\n".join(lineas) + "\n\nResponde con el numero:"


def _menu_obras():
    lineas = [f"{i+1}. {o}" for i, o in enumerate(OBRAS_SOCIALES)]
    return "Cobertura medica:\n\n" + "\n".join(lineas) + "\n\nResponde con el numero:"


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
        log.error(f"Error buscando turno: {e}")
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
    log.info(f"WA [{phone}] step={step} msg={txt[:50]}")

    # ── Comandos universales: solo "0" y "5" interrumpen cualquier flujo ──
    # Cualquier otra palabra de saludo/salida SOLO aplica cuando step=="menu"
    if txt == "5":
        _reset(sess, ws)
        _enviar(phone, DESPEDIDA)
        return

    if txt == "0":
        _reset(sess, ws)
        _enviar(phone, MENU)
        return

    # ── Si está en el menú y manda cualquier saludo → mostrar menú ──
    if step == "menu":
        SALUDOS = {"hola","buenas","buenos","hi","hello","ola","buen dia",
                   "buen día","buenas tardes","buenas noches","menu","menú",
                   "inicio","start","turno","quiero un turno"}
        if low in SALUDOS or txt not in ("1","2","3","4","5"):
            _enviar(phone, MENU)
            return

    # ════════════════════════════════════════════════════════
    # MENU PRINCIPAL
    # ════════════════════════════════════════════════════════
    if step == "menu":
        if txt == "1":
            sess["step"] = "nuevo_nombre"
            _save(sess, ws)
            _enviar(phone, "Nuevo turno\n\nIngresa tu nombre:")
        elif txt == "2":
            sess["step"] = "mod_buscar"
            _save(sess, ws)
            _enviar(phone, "Modificar turno\n\nIngresa el nombre con que sacaste el turno:")
        elif txt == "3":
            sess["step"] = "cancel_buscar"
            _save(sess, ws)
            _enviar(phone, "Cancelar turno\n\nIngresa el nombre con que sacaste el turno:")
        elif txt == "4":
            _enviar(phone, INFO)
        return

    # ════════════════════════════════════════════════════════
    # SACAR TURNO
    # ════════════════════════════════════════════════════════
    if step == "nuevo_nombre":
        if len(txt) < 2:
            _enviar(phone, "Nombre muy corto. Ingresa tu nombre:")
            return
        sess["nombre"] = txt.title()
        sess["step"]   = "nuevo_apellido"
        _save(sess, ws)
        _enviar(phone, f"Hola {sess['nombre']}!\n\nIngresa tu apellido:")
        return

    if step == "nuevo_apellido":
        if len(txt) < 2:
            _enviar(phone, "Apellido muy corto. Intenta de nuevo:")
            return
        sess["apellido"] = txt.title()
        sess["step"]     = "nuevo_dni"
        _save(sess, ws)
        _enviar(phone,
            f"{sess['nombre']} {sess['apellido']}\n\n"
            f"Cual es tu DNI? (solo numeros)\n"
            f"Si no tenes, responde: no"
        )
        return

    if step == "nuevo_dni":
        if low in ("no", "no tengo", "-", "n/a", "sin dni"):
            sess["dni"] = ""
        else:
            dni_limpio = re.sub(r"\D", "", txt)
            if len(dni_limpio) < 7:
                _enviar(phone, "DNI invalido. Ingresa solo numeros (ej: 32456789) o responde: no")
                return
            sess["dni"] = dni_limpio
        sess["step"] = "nuevo_obra_social"
        _save(sess, ws)
        _enviar(phone, _menu_obras())
        return

    if step == "nuevo_obra_social":
        if not txt.isdigit() or not (1 <= int(txt) <= len(OBRAS_SOCIALES)):
            _enviar(phone, f"Elige un numero del 1 al {len(OBRAS_SOCIALES)}.")
            return
        sess["obra_social"] = OBRAS_SOCIALES[int(txt) - 1]
        sess["step"]        = "nuevo_telefono"
        _save(sess, ws)
        tel_wa = _tel_desde_phone(phone)
        _enviar(phone,
            f"Cobertura: {sess['obra_social']}\n\n"
            f"Tu numero de WhatsApp es: +{tel_wa}\n\n"
            f"Responde SI para usar ese numero, o escribe otro:"
        )
        return

    if step == "nuevo_telefono":
        # IMPORTANTE: "si" y "sí" no deben estar en _SALIR_WORDS
        # porque son respuestas validas en este paso
        if low in ("si", "sí", "s", "yes", "ok", "correcto", "confirmo", "ese"):
            sess["telefono"] = _tel_desde_phone(phone)
        else:
            tel_limpio = re.sub(r"\D", "", txt)
            if len(tel_limpio) < 8:
                _enviar(phone, "Numero invalido. Ingresa el telefono o responde SI para confirmar el tuyo:")
                return
            sess["telefono"] = tel_limpio
        sess["step"] = "nuevo_email"
        _save(sess, ws)
        _enviar(phone, "Ingresa tu email para la confirmacion del turno:")
        return

    if step == "nuevo_email":
        if "@" not in txt or "." not in txt.split("@")[-1]:
            _enviar(phone, "Email invalido. Ejemplo: nombre@mail.com\nIntenta de nuevo:")
            return
        sess["email"] = txt.lower().strip()
        _save(sess, ws)
        # Buscar fechas disponibles
        try:
            disp, _ = _fechas_con_slots(sheet)
        except Exception as e:
            log.error(f"Error buscando fechas: {e}")
            _enviar(phone, "Error al consultar la agenda. Intenta en unos minutos.")
            return
        if not disp:
            _enviar(phone, "No hay fechas disponibles este mes.\nLlamanos al (3794) 34-9278.")
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
            _enviar(phone, f"Elige un numero del 1 al {min(len(disp), 10)}.")
            return
        fecha_elegida = disp[int(txt) - 1]
        try:
            _, oc   = _fechas_con_slots(sheet)
            slots   = _slots_para_fecha(fecha_elegida, oc)
        except Exception as e:
            log.error(f"Error buscando horarios: {e}")
            _enviar(phone, "Error al consultar horarios. Intenta de nuevo.")
            return
        if not slots:
            _enviar(phone, "Esa fecha se lleno. Elige otra:")
            _enviar(phone, _menu_fechas(disp))
            return
        sess["fecha"] = fecha_elegida
        sess["disp"]  = slots
        sess["step"]  = "nuevo_hora"
        _save(sess, ws)
        _enviar(phone, f"Fecha elegida: {fecha_elegida}\n\n" + _menu_horarios(slots))
        return

    if step == "nuevo_hora":
        slots = sess.get("disp", [])
        if not txt.isdigit() or not (1 <= int(txt) <= len(slots)):
            _enviar(phone, f"Elige un numero del 1 al {len(slots)}.")
            return
        hora_elegida = slots[int(txt) - 1]
        try:
            sheet.append_row([
                sess.get("nombre",""), sess.get("apellido",""), sess.get("dni",""),
                sess.get("obra_social",""), sess.get("telefono",""), sess.get("email",""),
                sess.get("fecha",""), hora_elegida, "Pendiente"
            ])
            log.info(f"Turno WA guardado: {sess['nombre']} {sess['apellido']} {sess['fecha']} {hora_elegida}")
        except Exception as e:
            log.error(f"Error guardando turno WA: {e}")
            _enviar(phone, "No se pudo guardar el turno. Llamanos al (3794) 34-9278.")
            return
        _enviar(phone,
            f"TURNO SOLICITADO!\n\n"
            f"Nombre: {sess['nombre']} {sess['apellido']}\n"
            f"Cobertura: {sess['obra_social']}\n"
            f"Telefono: {sess['telefono']}\n"
            f"Email: {sess['email']}\n"
            f"Fecha: {sess['fecha']}  Hora: {hora_elegida}hs\n\n"
            f"Te avisamos cuando este confirmado.\n\n"
            f"C. Pellegrini 799, Corrientes\n"
            f"Tel: (3794) 34-9278\n\n"
            f"Escribe 1 para nuevo turno o 5 para salir."
        )
        _reset(sess, ws)
        return

    # ════════════════════════════════════════════════════════
    # MODIFICAR TURNO
    # ════════════════════════════════════════════════════════
    if step == "mod_buscar":
        fila, t = _buscar_turno(sheet, txt)
        if not t:
            _enviar(phone, "No encontre turno con ese nombre.\nEscribe 0 para volver al menu.")
            _reset(sess, ws)
            return
        sess["fila_turno"] = fila
        try:
            disp, _ = _fechas_con_slots(sheet)
        except Exception as e:
            log.error(f"Error fechas modificar: {e}")
            _enviar(phone, "Error consultando agenda.")
            return
        sess["disp"] = disp
        sess["step"] = "mod_fecha"
        _save(sess, ws)
        _enviar(phone,
            f"Turno actual:\n"
            f"Nombre: {t.get('Nombre','')} {t.get('Apellido','')}\n"
            f"Fecha: {t.get('Fecha','')}  Hora: {t.get('Hora','')}hs\n\n"
            + _menu_fechas(disp)
        )
        return

    if step == "mod_fecha":
        disp = sess.get("disp", [])
        if not txt.isdigit() or not (1 <= int(txt) <= len(disp)):
            _enviar(phone, f"Elige un numero del 1 al {min(len(disp), 10)}.")
            return
        fecha_elegida = disp[int(txt) - 1]
        try:
            _, oc = _fechas_con_slots(sheet)
            slots = _slots_para_fecha(fecha_elegida, oc)
        except Exception as e:
            log.error(f"Error horarios modificar: {e}")
            _enviar(phone, "Error consultando horarios.")
            return
        sess["fecha"] = fecha_elegida
        sess["disp"]  = slots
        sess["step"]  = "mod_hora"
        _save(sess, ws)
        _enviar(phone, f"Fecha: {fecha_elegida}\n\n" + _menu_horarios(slots))
        return

    if step == "mod_hora":
        slots = sess.get("disp", [])
        if not txt.isdigit() or not (1 <= int(txt) <= len(slots)):
            _enviar(phone, f"Elige un numero del 1 al {len(slots)}.")
            return
        hora_elegida = slots[int(txt) - 1]
        fila = sess.get("fila_turno", 0)
        try:
            sheet.update(f'G{fila}:I{fila}', [[sess["fecha"], hora_elegida, "Pendiente"]])
        except Exception as e:
            log.error(f"Error modificando turno: {e}")
            _enviar(phone, "Error al modificar. Llamanos al (3794) 34-9278.")
            return
        _enviar(phone,
            f"TURNO MODIFICADO!\n\n"
            f"Nueva fecha: {sess['fecha']}\n"
            f"Nueva hora: {hora_elegida}hs\n\n"
            f"Te confirmaremos a la brevedad.\n"
            f"Escribe 0 para el menu o 5 para salir."
        )
        _reset(sess, ws)
        return

    # ════════════════════════════════════════════════════════
    # CANCELAR TURNO
    # ════════════════════════════════════════════════════════
    if step == "cancel_buscar":
        fila, t = _buscar_turno(sheet, txt)
        if not t:
            _enviar(phone, "No encontre turno con ese nombre.\nEscribe 0 para volver al menu.")
            _reset(sess, ws)
            return
        sess["fila_turno"] = fila
        sess["step"]       = "cancel_conf"
        _save(sess, ws)
        _enviar(phone,
            f"Confirmas la cancelacion?\n\n"
            f"Nombre: {t.get('Nombre','')} {t.get('Apellido','')}\n"
            f"Fecha: {t.get('Fecha','')}  Hora: {t.get('Hora','')}hs\n\n"
            f"Responde SI o NO."
        )
        return

    if step == "cancel_conf":
        if low in ("si", "sí", "s", "yes"):
            fila = sess.get("fila_turno", 0)
            try:
                sheet.update_cell(fila, IDX_ESTADO + 1, "Cancelado")
            except Exception as e:
                log.error(f"Error cancelando: {e}")
                _enviar(phone, "Error al cancelar. Llamanos al (3794) 34-9278.")
                return
            _enviar(phone, "Turno CANCELADO.\n\nEscribe 1 para sacar un nuevo turno o 5 para salir.")
        else:
            _enviar(phone, "Cancelacion abortada. Tu turno sigue activo.\nEscribe 0 para el menu.")
        _reset(sess, ws)
        return

    # Step desconocido → volver al menu
    log.warning(f"Step desconocido '{step}' para {phone}")
    _reset(sess, ws)
    _enviar(phone, MENU)
