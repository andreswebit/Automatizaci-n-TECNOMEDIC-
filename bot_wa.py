import re, os, requests, logging
from datetime import datetime
from calendar import monthrange

log = logging.getLogger(__name__)

TWILIO_SID     = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN   = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_WA_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

# ─── Sesiones en la hoja "Sesiones" de Google Sheets ─────────────
# Columnas: Phone | Step | Nombre | Fecha | Hora | Disp | Fila
# Esto permite que funcione con múltiples workers de gunicorn

def _get_sheet_sesiones(sheet):
    """Obtiene o crea la hoja 'Sesiones' en el mismo spreadsheet."""
    try:
        return sheet.spreadsheet.worksheet("Sesiones")
    except Exception:
        ws = sheet.spreadsheet.add_worksheet(title="Sesiones", rows=200, cols=10)
        ws.append_row(["Phone", "Step", "Nombre", "Fecha", "Hora", "Disp", "Fila"])
        return ws

def _get_session(phone, sheet):
    ws = _get_sheet_sesiones(sheet)
    rows = ws.get_all_values()
    for i, row in enumerate(rows[1:], 2):
        if row and row[0] == phone:
            return {
                "row_ws": i,
                "step":   row[1] if len(row) > 1 else "menu",
                "data": {
                    "nombre": row[2] if len(row) > 2 else "",
                    "fecha":  row[3] if len(row) > 3 else "",
                    "hora":   row[4] if len(row) > 4 else "",
                    "disp":   row[5].split(",") if len(row) > 5 and row[5] else [],
                    "fila":   int(row[6]) if len(row) > 6 and row[6] else 0,
                }
            }, ws
    # Crear nueva sesión
    ws.append_row([phone, "menu", "", "", "", "", ""])
    rows2 = ws.get_all_values()
    return {
        "row_ws": len(rows2),
        "step": "menu",
        "data": {"nombre": "", "fecha": "", "hora": "", "disp": [], "fila": 0}
    }, ws

def _save_session(sess, ws):
    r = sess["row_ws"]
    d = sess["data"]
    ws.update(f"A{r}:G{r}", [[
        sess.get("phone", ""),
        sess["step"],
        d.get("nombre", ""),
        d.get("fecha", ""),
        d.get("hora", ""),
        ",".join(d.get("disp", [])),
        str(d.get("fila", ""))
    ]])

def _reset_session(sess, ws):
    r = sess["row_ws"]
    ws.update(f"A{r}:G{r}", [[sess.get("phone", ""), "menu", "", "", "", "", ""]])

# ─── Helpers ──────────────────────────────────────────────────────
HORARIOS = [f"{h:02d}:00" for h in range(8, 18)]

MENU   = "🏥 *TECNOMEDIC*\n\n1️⃣ Sacar turno\n2️⃣ Modificar\n3️⃣ Cancelar\n4️⃣ Info\n0️⃣ Menú"
INFO   = "ℹ️ *TECNOMEDIC*\nHorarios: Lun-Vie 8 a 17hs\n📍 Pellegrini 799, Corrientes\n📞 (3794) 34-9278"
CANCEL = "⚠️ ¿Confirmás cancelación?\n👤 {nombre}\n📅 {fecha} ⏰ {hora}\n\nRespondé *SI* o *NO*"

def _enviar(to, body):
    if not TWILIO_SID:
        return False
    try:
        r = requests.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json",
            data={"From": TWILIO_WA_FROM, "To": to, "Body": body},
            auth=(TWILIO_SID, TWILIO_TOKEN),
            timeout=10
        )
        return r.status_code == 201
    except:
        return False

def _validar_fecha(t):
    t = t.strip().replace("-", "/")
    try:
        d = datetime.strptime(t, "%d/%m/%Y")
        return None if d.date() < datetime.today().date() else d.strftime("%d/%m/%Y")
    except:
        return None

def _ocupados(sheet):
    o = {}
    rows = sheet.get_all_values()
    if len(rows) < 2:
        return o
    h = rows[0]
    i_f = h.index("Fecha") if "Fecha" in h else 3
    i_h = h.index("Hora")  if "Hora"  in h else 4
    i_e = h.index("Estado") if "Estado" in h else 5
    for r in rows[1:]:
        if len(r) <= max(i_f, i_h):
            continue
        estado = r[i_e].lower() if len(r) > i_e else ""
        if estado == "cancelado":
            continue
        o.setdefault(r[i_f].strip(), set()).add(r[i_h].strip())
    return o

def _fechas_disponibles(sheet):
    hoy = datetime.today().date()
    y, m = hoy.year, hoy.month
    _, ld = monthrange(y, m)
    oc = _ocupados(sheet)
    disp = []
    for d in range(hoy.day, ld + 1):
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
    rows = sheet.get_all_values()
    if len(rows) < 2:
        return None, None
    h = rows[0]
    for i, r in enumerate(rows[1:], 2):
        if len(r) < len(h):
            r += [""] * (len(h) - len(r))
        t = dict(zip(h, r))
        if nombre.lower() in t.get("Nombre", "").lower() and t.get("Estado", "").lower() != "cancelado":
            return i, t
    return None, None


# ─── Procesador principal ─────────────────────────────────────────

def procesar(phone, msg, sheet):
    sess, ws = _get_session(phone, sheet)
    sess["phone"] = phone
    txt  = msg.strip()
    low  = txt.lower()
    step = sess["step"]

    # Siempre puede volver al menú
    if txt in ("0", "menu", "menú"):
        _reset_session(sess, ws)
        _enviar(phone, MENU)
        return

    # ── MENÚ PRINCIPAL ────────────────────────────────────────────
    if step == "menu":
        if txt == "1":
            sess["step"] = "nuevo_nombre"
            _save_session(sess, ws)
            _enviar(phone, "📝 *Nuevo turno*\n\nIngresá tu nombre completo:")
            return
        if txt == "2":
            sess["step"] = "mod_nombre"
            _save_session(sess, ws)
            _enviar(phone, "🔍 *Modificar turno*\n\nIngresá tu nombre:")
            return
        if txt == "3":
            sess["step"] = "cancel_nombre"
            _save_session(sess, ws)
            _enviar(phone, "❌ *Cancelar turno*\n\nIngresá tu nombre:")
            return
        if txt == "4":
            _enviar(phone, INFO)
            return
        _enviar(phone, MENU)
        return

    # ── SACAR TURNO ───────────────────────────────────────────────
    if step == "nuevo_nombre":
        sess["data"]["nombre"] = txt.title()
        try:
            disp, _ = _fechas_disponibles(sheet)
        except Exception as e:
            log.error(f"Error leyendo fechas: {e}")
            _enviar(phone, "❌ Error al consultar la agenda. Intentá de nuevo.")
            return
        if not disp:
            _enviar(phone, "😔 No hay fechas disponibles este mes. Llamanos al (3794) 34-9278.")
            _reset_session(sess, ws)
            return
        sess["data"]["disp"] = disp
        sess["step"] = "nuevo_fecha"
        _save_session(sess, ws)
        lista = "\n".join(disp[:10])
        _enviar(phone, f"📅 *Fechas disponibles:*\n{lista}\n\nEscribí la fecha en formato *dd/mm/aaaa*:")
        return

    if step == "nuevo_fecha":
        f = _validar_fecha(txt)
        disp = sess["data"].get("disp", [])
        if not f or f not in disp:
            _enviar(phone, "⚠️ Fecha no válida o no disponible. Elegí una de la lista o escribí en formato dd/mm/aaaa.")
            return
        try:
            _, oc = _fechas_disponibles(sheet)
            h = _asignar_hora(f, oc)
        except Exception as e:
            log.error(f"Error asignando hora: {e}")
            _enviar(phone, "❌ Error al asignar horario. Intentá de nuevo.")
            return
        if not h:
            _enviar(phone, "😔 No hay horarios libres para esa fecha. Elegí otra.")
            return
        sess["data"]["fecha"] = f
        sess["data"]["hora"]  = h
        sess["step"] = "nuevo_email"
        _save_session(sess, ws)
        _enviar(phone, f"✅ Fecha *{f}* · Hora *{h}*\n\nIngresá tu email para la confirmación:")
        return

    if step == "nuevo_email":
        d = sess["data"]
        tel = re.sub(r"\D", "", phone)
        try:
            sheet.append_row([d["nombre"], tel, txt, d["fecha"], d["hora"], "Pendiente"])
        except Exception as e:
            log.error(f"Error guardando turno: {e}")
            _enviar(phone, "❌ Error al guardar el turno. Intentá de nuevo.")
            return
        _enviar(phone, f"🎉 *Turno solicitado!*\n\n👤 {d['nombre']}\n📅 {d['fecha']} ⏰ {d['hora']}\n\nTe confirmaremos a la brevedad. ¡Gracias!")
        _reset_session(sess, ws)
        return

    # ── MODIFICAR ─────────────────────────────────────────────────
    if step == "mod_nombre":
        fila, t = _buscar_turno(sheet, txt)
        if not t:
            _enviar(phone, "🔍 No encontré un turno con ese nombre. Intentá de nuevo o escribí 0 para volver.")
            _reset_session(sess, ws)
            return
        sess["data"]["fila"] = fila
        try:
            disp, _ = _fechas_disponibles(sheet)
        except Exception as e:
            log.error(f"Error leyendo fechas: {e}")
            _enviar(phone, "❌ Error al consultar la agenda.")
            return
        sess["data"]["disp"] = disp
        sess["step"] = "mod_fecha"
        _save_session(sess, ws)
        lista = "\n".join(disp[:10])
        _enviar(phone, f"📅 *Nuevas fechas disponibles:*\n{lista}\n\nEscribí la nueva fecha en formato *dd/mm/aaaa*:")
        return

    if step == "mod_fecha":
        f = _validar_fecha(txt)
        if not f:
            _enviar(phone, "⚠️ Fecha no válida. Escribí en formato dd/mm/aaaa.")
            return
        try:
            _, oc = _fechas_disponibles(sheet)
            h = _asignar_hora(f, oc)
        except Exception as e:
            log.error(f"Error asignando hora: {e}")
            _enviar(phone, "❌ Error al asignar horario.")
            return
        fila = sess["data"]["fila"]
        try:
            sheet.update_cell(fila, 4, f)
            sheet.update_cell(fila, 5, h)
            sheet.update_cell(fila, 6, "Pendiente")
        except Exception as e:
            log.error(f"Error modificando turno: {e}")
            _enviar(phone, "❌ Error al modificar el turno.")
            return
        _enviar(phone, f"✏️ *Turno modificado!*\n📅 {f} ⏰ {h}\n\nTe confirmamos a la brevedad.")
        _reset_session(sess, ws)
        return

    # ── CANCELAR ──────────────────────────────────────────────────
    if step == "cancel_nombre":
        fila, t = _buscar_turno(sheet, txt)
        if not t:
            _enviar(phone, "🔍 No encontré un turno con ese nombre. Escribí 0 para volver.")
            _reset_session(sess, ws)
            return
        sess["data"]["fila"] = fila
        sess["step"] = "cancel_conf"
        _save_session(sess, ws)
        _enviar(phone, CANCEL.format(
            nombre=t.get("Nombre", ""),
            fecha=t.get("Fecha", ""),
            hora=t.get("Hora", "")
        ))
        return

    if step == "cancel_conf":
        if low in ("si", "s", "yes", "sí"):
            fila = sess["data"]["fila"]
            try:
                sheet.update_cell(fila, 6, "Cancelado")
            except Exception as e:
                log.error(f"Error cancelando turno: {e}")
                _enviar(phone, "❌ Error al cancelar.")
                return
            _enviar(phone, "✅ Turno *cancelado*. Si necesitás otro turno escribí 1.")
        else:
            _enviar(phone, "👍 Cancelación abortada. Tu turno sigue activo.")
        _reset_session(sess, ws)
        return

    # Step desconocido → resetear
    _reset_session(sess, ws)
    _enviar(phone, MENU)
