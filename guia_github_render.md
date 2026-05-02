# Guía de Deploy — GitHub + Render
# TECNOMEDIC · Sistema de Turnos
# =====================================================================
# URL de prueba: https://tecnomedic-turnos.onrender.com
# URL definitiva (después): https://turnos.tecnomedic.com.ar
# =====================================================================


## PARTE 1 — Subir el código a GitHub

### 1.1 Crear cuenta GitHub (si no tenés)
  https://github.com/signup
  → Usá tu email personal o del proyecto


### 1.2 Crear el repositorio en GitHub
  1. Ir a: https://github.com/new
  2. Repository name:  tecnomedic-turnos
  3. Visibility:       Private  ← IMPORTANTE: privado para proteger el código
  4. NO marcar ninguna opción de README/gitignore (ya los tenemos)
  5. Click "Create repository"


### 1.3 Subir el código desde tu PC
  Abrí una terminal en la carpeta del proyecto y ejecutá:

  git init
  git add .
  git commit -m "feat: sistema de turnos TECNOMEDIC v1.0"
  git branch -M main
  git remote add origin https://github.com/TU-USUARIO/tecnomedic-turnos.git
  git push -u origin main

  ⚠️  Verificar antes de hacer push que estos archivos NO estén incluidos:
      git status
      → credenciales.json y .env NO deben aparecer en la lista


### 1.4 Verificar que los archivos sensibles no subieron
  Ir a https://github.com/TU-USUARIO/tecnomedic-turnos
  → credenciales.json NO debe aparecer
  → .env NO debe aparecer
  → Si aparecen: avisarme antes de continuar


## PARTE 2 — Deploy en Render

### 2.1 Crear cuenta Render
  https://render.com
  → Registrarse con GitHub (más fácil, conecta directo)


### 2.2 Crear el servicio web
  1. Dashboard → "New +" → "Web Service"
  2. Seleccionar "Build and deploy from a Git repository"
  3. Conectar el repo: tecnomedic-turnos
  4. Configuración:
     - Name:          tecnomedic-turnos
     - Region:        Oregon (US West) o el más cercano disponible
     - Branch:        main
     - Runtime:       Python 3
     - Build Command: pip install -r requirements.txt
     - Start Command: gunicorn app:app -c gunicorn.conf.py
  5. Click "Create Web Service"


### 2.3 Cargar las variables de entorno
  En el servicio creado → pestaña "Environment" → "Add Environment Variable"

  ┌─────────────────────┬──────────────────────────────────────┐
  │ Variable            │ Valor                                │
  ├─────────────────────┼──────────────────────────────────────┤
  │ SECRET_KEY          │ (generada automática por Render)     │
  │ ADMIN_USER          │ admin                                │
  │ ADMIN_PASSWORD      │ TuClaveSegura2025!                   │
  │ N8N_WEBHOOK_URL     │ http://localhost:5678/webhook/...    │
  │                     │ (actualizar cuando n8n esté en prod) │
  └─────────────────────┴──────────────────────────────────────┘


### 2.4 Subir credenciales.json como Secret File
  En "Environment" → "Secret Files" → "Add Secret File"
  - Filename:  credenciales.json
  - Contents:  pegar el contenido del archivo credenciales.json

  ⚠️  IMPORTANTE: primero regenerar las credenciales en Google Cloud
      (las actuales quedaron expuestas en una conversación anterior)


### 2.5 Trigger del primer deploy
  Render arranca el deploy automáticamente.
  Seguir el log en: Dashboard → tecnomedic-turnos → "Logs"

  El deploy exitoso se ve así:
  ✓  Build successful
  ✓  Your service is live 🎉


### 2.6 Probar la app
  Abrir: https://tecnomedic-turnos.onrender.com
  → Formulario de turnos visible ✅

  Abrir: https://tecnomedic-turnos.onrender.com/admin
  → Redirige al login ✅

  Ingresar con ADMIN_USER y ADMIN_PASSWORD configurados ✅


## PARTE 3 — Cuando tengas acceso a NIC.ar

  1. En Render → Dashboard → tecnomedic-turnos → Settings → Custom Domains
  2. Click "Add Custom Domain"
  3. Escribir: turnos.tecnomedic.com.ar
  4. Render te da un valor CNAME, algo como:
     tecnomedic-turnos.onrender.com
  5. En NIC.ar → tecnomedic.com.ar → Configurar DNS → Agregar:
     Tipo:   CNAME
     Nombre: turnos
     Valor:  tecnomedic-turnos.onrender.com
     TTL:    3600
  6. Esperar 15-30 minutos
  7. Render activa HTTPS automáticamente ✅

  Resultado:
    https://turnos.tecnomedic.com.ar  →  app funcionando con HTTPS ✅
    https://www.tecnomedic.com.ar     →  sitio del cliente sin cambios ✅


## PARTE 4 — Actualizar la app en el futuro

  Cada vez que modifiques el código:
  
  git add .
  git commit -m "descripción del cambio"
  git push

  → Render detecta el push y hace el redeploy automáticamente en ~2 minutos


## RESUMEN DE URLs

  GitHub (código):   https://github.com/TU-USUARIO/tecnomedic-turnos
  Render (prueba):   https://tecnomedic-turnos.onrender.com
  Producción:        https://turnos.tecnomedic.com.ar  (cuando tengas NIC.ar)
  Admin:             /admin  (en cualquiera de las URLs anteriores)
