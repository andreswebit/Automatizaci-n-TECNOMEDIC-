# TECNOMEDIC – Sistema de Turnos
Automatización de Turnos · Cámara Hiperbárica · Corrientes, Argentina

---

## Estructura del proyecto

```
tecnomedic/
├── app.py                  # Aplicación Flask principal
├── gunicorn.conf.py        # Config servidor de producción
├── Procfile                # Comando de inicio (Render/Railway)
├── render.yaml             # Config despliegue en Render
├── requirements.txt        # Dependencias Python
├── .env                    # Variables de entorno (NO subir a Git)
├── env.example             # Plantilla del .env
├── .gitignore
├── credenciales.json       # Service account Google (NO subir a Git)
├── static/
│   ├── tecnomedic.css
│   ├── tecno_logo.png
│   ├── fondo1.JPG
│   ├── fondo2.jfif
│   └── fondo3.webp
├── templates/
│   ├── form.html           # Formulario de turnos (público)
│   ├── confirmacion.html   # Pantalla post-solicitud
│   ├── admin.html          # Dashboard admin (requiere login)
│   └── login.html          # Login admin
└── n8n/
    ├── solicitur_turno_tecnomedic.json
    ├── confirmar_turno_Tecnomedic.json
    └── cron.json
```

---

## Desarrollo local

```bash
# 1. Clonar el repositorio
git clone https://github.com/tu-usuario/tecnomedic-turnos.git
cd tecnomedic-turnos

# 2. Crear entorno virtual
python -m venv venv
source venv/bin/activate        # Linux/Mac
venv\Scripts\activate           # Windows

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar variables de entorno
cp env.example .env
# Editar .env con tus valores reales

# 5. Agregar credenciales de Google
# Copiar credenciales.json en la raíz del proyecto

# 6. Correr la app
python app.py
# → http://localhost:5000
```

---

## Despliegue en Render (producción)

### Paso 1 — Subir a GitHub
```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/tu-usuario/tecnomedic-turnos.git
git push -u origin main
```

### Paso 2 — Crear servicio en Render
1. Entrar a [render.com](https://render.com) → **New** → **Web Service**
2. Conectar el repositorio de GitHub
3. Render detecta `render.yaml` automáticamente

### Paso 3 — Variables de entorno en Render
En el dashboard del servicio → **Environment** → agregar:

| Variable | Valor |
|---|---|
| `SECRET_KEY` | (generada automática) |
| `ADMIN_USER` | tu_usuario |
| `ADMIN_PASSWORD` | tu_clave_segura |
| `N8N_WEBHOOK_URL` | https://tu-n8n.com/webhook/turnos-tecnomedic |

### Paso 4 — Credenciales Google
En Render → **Environment** → **Secret Files** → subir `credenciales.json`

### Paso 5 — Deploy
Render hace el deploy automáticamente. La app queda en:
`https://tecnomedic-turnos.onrender.com`

---

## Etapas del proyecto

- [x] Etapa 1 — Seguridad (login admin, .gitignore, claves en .env)
- [x] Etapa 2 — Producción (Gunicorn, Render, variables de entorno)
- [x] Etapa 3 — Dominio propio y HTTPS
- [ ] Etapa 4 — WhatsApp (Twilio) — código comentado, listo para activar
- [ ] Etapa 5 — n8n en producción

---

## Etapa 3 — Dominio y HTTPS

**Dominio:** `turnos.tecnomedic.com.ar`
**Registrador:** NIC.ar
**Estrategia:** agregar subdominio `turnos` al dominio existente,
sin tocar el sitio principal `www.tecnomedic.com.ar`.

### URLs finales
| URL | Qué es |
|---|---|
| `https://turnos.tecnomedic.com.ar` | Formulario público de turnos |
| `https://turnos.tecnomedic.com.ar/admin` | Panel admin (requiere login) |
| `https://www.tecnomedic.com.ar` | Sitio actual del cliente (sin cambios) |

### Registro DNS a agregar en NIC.ar
```
Tipo:   CNAME  (Render)  o  A  (VPS)
Nombre: turnos
Valor:  tecnomedic-turnos.onrender.com  o  IP_DEL_VPS
TTL:    3600
```
Ver guía completa: `guia_dns_nicar.md`

### Opción A — Render (recomendado)
1. Subir código a GitHub
2. Conectar repo en [render.com](https://render.com) → New Web Service
3. Dashboard → Settings → Custom Domains → `turnos.tecnomedic.com.ar`
4. Agregar registro CNAME en NIC.ar con el valor que da Render
5. HTTPS se activa automático ✅

### Opción B — VPS propio
```bash
# 1. Agregar registro A en NIC.ar apuntando a la IP del VPS
# 2. Esperar propagación DNS (15min - 24hs)
# 3. Correr en el servidor:
chmod +x deploy.sh && sudo ./deploy.sh
# Instala Nginx + Certbot + SSL + systemd automáticamente
```

---

## Activar WhatsApp (cuando estés listo)

1. Descomentar en `requirements.txt`: `twilio>=8.0.0`
2. Descomentar en `app.py`: imports, funciones y llamadas marcadas con `◀ WhatsApp`
3. Agregar en `.env` y en Render:
   ```
   TWILIO_ACCOUNT_SID=ACxxxxxxxx
   TWILIO_AUTH_TOKEN=xxxxxxxx
   TWILIO_WHATSAPP_FROM=whatsapp:+549XXXXXXXXXX
   ```
4. Descomentar en `render.yaml` las variables de Twilio
