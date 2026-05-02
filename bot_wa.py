import re, os, requests, logging
from datetime import datetime
from calendar import monthrange

log = logging.getLogger(__name__)

TWILIO_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_WA_FROM = os.environ.get("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

_sessions = {}
HORARIOS = [f"{h:02d}:00" for h in range(8,18)]

MENU = "🏥 *TECNOMEDIC*\n\n1️⃣ Sacar turno\n2️⃣ Modificar\n3️⃣ Cancelar\n4️⃣ Info\n0️⃣ Menú"
INFO = "ℹ️ TECNOMEDIC\nHorarios Lun-Vie 8 a 17\nPellegrini 799"
CANCEL = "⚠️ Confirmas cancelacion?\n👤 {nombre}\n📅 {fecha} ⏰ {hora}\n\nSI o NO"

def _get_session(p): return _sessions.setdefault(p, {"step":"menu","data":{}})
def _reset(p): _sessions[p]={"step":"menu","data":{}}
def _enviar(to,body):
    if not TWILIO_SID: return False
    try:
        r=requests.post(f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json", data={"From":TWILIO_WA_FROM,"To":to,"Body":body}, auth=(TWILIO_SID,TWILIO_TOKEN), timeout=10)
        return r.status_code==201
    except: return False

def _validar_fecha(t):
    t=t.strip().replace("-","/")
    try:
        d=datetime.strptime(t,"%d/%m/%Y")
        return None if d.date()<datetime.today().date() else d.strftime("%d/%m/%Y")
    except: return None

def _ocupados(sheet):
    o={}; rows=sheet.get_all_values()
    if len(rows)<2: return o
    h=rows[0]; i_f=3; i_h=4; i_e=5
    if "Fecha" in h: i_f=h.index("Fecha")
    if "Hora" in h: i_h=h.index("Hora")
    if "Estado" in h: i_e=h.index("Estado")
    for r in rows[1:]:
        if len(r)<=max(i_f,i_h): continue
        if r[i_e].lower()=="cancelado": continue
        o.setdefault(r[i_f].strip(), set()).add(r[i_h].strip())
    return o

def _fechas(sheet):
    hoy=datetime.today().date(); y,m=hoy.year,hoy.month; _,ld=monthrange(y,m); oc=_ocupados(sheet); disp=[]
    for d in range(hoy.day, ld+1):
        dt=datetime(y,m,d).date()
        if dt.weekday()>=5: continue
        f=dt.strftime("%d/%m/%Y")
        if len(HORARIOS)-len(oc.get(f,set()))>0: disp.append(f)
    return disp, oc

def _asignar(fecha,oc):
    for h in HORARIOS:
        if h not in oc.get(fecha,set()): return h
    return None

def _buscar(sheet,nombre):
    rows=sheet.get_all_values()
    if len(rows)<2: return None,None
    h=rows[0]
    for i,r in enumerate(rows[1:],2):
        if len(r)<len(h): r+=[""]*(len(h)-len(r))
        t=dict(zip(h,r))
        if nombre.lower() in t.get("Nombre","").lower() and t.get("Estado","").lower()!="cancelado": return i,t
    return None,None

def procesar(phone,msg,sheet):
    sess=_get_session(phone); txt=msg.strip(); low=txt.lower()
    if txt in ("0","menu","menú"): _reset(phone); _enviar(phone,MENU); return
    step=sess["step"]
    if step=="menu":
        if txt=="1": sess["step"]="nuevo_nombre"; _enviar(phone,"📝 Nuevo turno\n\nNombre completo:"); return
        if txt=="2": sess["step"]="mod_nombre"; _enviar(phone,"🔍 Modificar\n\nNombre:"); return
        if txt=="3": sess["step"]="cancel_nombre"; _enviar(phone,"❌ Cancelar\n\nNombre:"); return
        if txt=="4": _enviar(phone,INFO); _reset(phone); return
        _enviar(phone,MENU); return
    if step=="nuevo_nombre":
        sess["data"]["nombre"]=txt.title(); disp,_=_fechas(sheet); sess["data"]["disp"]=disp
        lista="\n".join(disp[:12]); sess["step"]="nuevo_fecha"; _enviar(phone,"Fechas libres:\n"+lista+"\n\nEscribi dd/mm/aaaa:"); return
    if step=="nuevo_fecha":
        f=_validar_fecha(txt)
        if not f or f not in sess["data"]["disp"]: _enviar(phone,"Fecha no valida"); return
        _,oc=_fechas(sheet); h=_asignar(f,oc); sess["data"].update({"fecha":f,"hora":h}); sess["step"]="nuevo_email"; _enviar(phone,f"Fecha {f} hora {h}\nEmail?"); return
    if step=="nuevo_email":
        d=sess["data"]; tel=re.sub(r"\D","",phone); sheet.append_row([d["nombre"],tel,txt,d["fecha"],d["hora"],"Pendiente"]); _enviar(phone,f"✅ Turno {d['fecha']} {d['hora']}"); _reset(phone); return
    if step=="mod_nombre":
        fila,t=_buscar(sheet,txt)
        if not t: _enviar(phone,"No encontrado"); _reset(phone); return
        sess["data"]["fila"]=fila; disp,_=_fechas(sheet); sess["data"]["disp"]=disp; lista="\n".join(disp[:12]); sess["step"]="mod_fecha"; _enviar(phone,"Nuevas fechas:\n"+lista); return
    if step=="mod_fecha":
        f=_validar_fecha(txt)
        if not f: _enviar(phone,"Fecha mala"); return
        _,oc=_fechas(sheet); h=_asignar(f,oc); fila=sess["data"]["fila"]; sheet.update_cell(fila,4,f); sheet.update_cell(fila,5,h); _enviar(phone,f"Modificado {f} {h}"); _reset(phone); return
    if step=="cancel_nombre":
        fila,t=_buscar(sheet,txt)
        if not t: _enviar(phone,"No encontrado"); _reset(phone); return
        sess["data"]["fila"]=fila; sess["step"]="cancel_conf"; _enviar(phone,CANCEL.format(nombre=t["Nombre"],fecha=t["Fecha"],hora=t["Hora"])); return
    if step=="cancel_conf":
        if low in ("si","s","yes"): sheet.update_cell(sess["data"]["fila"],6,"Cancelado"); _enviar(phone,"Cancelado")
        else: _enviar(phone,"Abortado")
        _reset(phone); return
