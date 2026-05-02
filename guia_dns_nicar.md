# Guía DNS NIC.ar — turnos.tecnomedic.com.ar
# =====================================================================
# El dominio tecnomedic.com.ar ya existe.
# Solo necesitamos agregar el subdominio "turnos" 
# para el sistema de turnos, SIN tocar el sitio principal.
# =====================================================================


## PASO 1 — Ingresar a NIC.ar

1. Entrar a: https://nic.ar
2. Iniciar sesión con el CUIT/CUIL del titular del dominio
   (puede ser el cliente o la empresa TECNOMEDIC)
3. Ir a: Mi cuenta → Dominios → tecnomedic.com.ar → Configurar DNS


## PASO 2 — Verificar los DNS actuales

Antes de tocar nada, anotá los registros existentes para no romper
el sitio actual de tecnomedic.com.ar.

Podés verificarlos también desde la terminal:
  nslookup tecnomedic.com.ar
  dig tecnomedic.com.ar A


## PASO 3 — Agregar el subdominio "turnos"

En la sección "Registros DNS" de NIC.ar, agregá UNO de estos según
la opción de hosting que elijas:

┌─────────────────────────────────────────────────────────────┐
│ OPCIÓN A — Render.com (más simple, recomendado)             │
│                                                             │
│  Tipo:   CNAME                                              │
│  Nombre: turnos                                             │
│  Valor:  tecnomedic-turnos.onrender.com   ← tu URL Render   │
│  TTL:    3600                                               │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ OPCIÓN B — VPS propio (DigitalOcean / Linode)               │
│                                                             │
│  Tipo:   A                                                  │
│  Nombre: turnos                                             │
│  Valor:  XXX.XXX.XXX.XXX   ← IP del servidor               │
│  TTL:    3600                                               │
└─────────────────────────────────────────────────────────────┘

⚠️  NO modificar los registros existentes del dominio raíz
    (tecnomedic.com.ar, www.tecnomedic.com.ar) para no afectar
    el sitio web actual del cliente.


## PASO 4 — Guardar y esperar propagación

Después de guardar en NIC.ar, los cambios pueden tardar entre
15 minutos y 24 horas en propagarse globalmente.

Para verificar que ya propagó:
  nslookup turnos.tecnomedic.com.ar
  → Debe devolver la IP del servidor o el CNAME de Render


## PASO 5 — Certificado SSL (HTTPS)

### Si usás Render:
  HTTPS se activa automáticamente al agregar el dominio en:
  Dashboard → Settings → Custom Domains → Add Custom Domain
  → Escribir: turnos.tecnomedic.com.ar

### Si usás VPS:
  El script deploy.sh ya instala Let's Encrypt automáticamente.
  Solo asegurate de que el DNS haya propagado antes de correrlo,
  porque Let's Encrypt necesita resolver el dominio para dar el certificado.

  Verificar propagación antes de correr deploy.sh:
    curl -I http://turnos.tecnomedic.com.ar
    → Debe responder (aunque sea con error 502, significa que llega)


## RESULTADO FINAL

  https://turnos.tecnomedic.com.ar        → Formulario de turnos (público)
  https://turnos.tecnomedic.com.ar/admin  → Panel admin (requiere login)
  https://www.tecnomedic.com.ar           → Sitio actual del cliente (sin cambios)


## CREDENCIALES NIC.ar — ¿Quién tiene el acceso?

Si el cliente no recuerda las credenciales de NIC.ar, puede recuperarlas en:
  https://nic.ar → "Olvidé mi contraseña"
  → Necesita acceso al email o teléfono registrado al momento de comprar el dominio

Si el dominio venció o hay problema de titularidad:
  Contactar a NIC.ar: https://nic.ar/static/files/contacto.html
