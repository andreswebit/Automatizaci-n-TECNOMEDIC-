#!/bin/bash
# =============================================================
#  update.sh — Actualizar la app en producción
#
#  Uso (desde el servidor):
#    sudo ./update.sh
#
#  Lo que hace:
#    1. Baja el último código de GitHub
#    2. Actualiza dependencias si cambiaron
#    3. Reinicia el servicio sin downtime perceptible
# =============================================================

set -e
APP_DIR="/var/www/tecnomedic"

echo "▶ Bajando últimos cambios..."
cd "$APP_DIR"
git pull origin main

echo "▶ Actualizando dependencias..."
"$APP_DIR/venv/bin/pip" install --quiet -r requirements.txt

echo "▶ Reiniciando servicio..."
systemctl restart tecnomedic
systemctl reload nginx

echo "✅ Actualización completada."
systemctl status tecnomedic --no-pager
