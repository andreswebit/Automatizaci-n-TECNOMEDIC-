# gunicorn.conf.py
# Configuración de Gunicorn para producción

import multiprocessing

# Dirección y puerto
bind    = "0.0.0.0:8000"

# Workers: (2 × núcleos) + 1  es la fórmula recomendada
workers = multiprocessing.cpu_count() * 2 + 1

# Tipo de worker
worker_class = "sync"

# Timeouts
timeout      = 120
keepalive    = 5

# Logs
accesslog = "-"   # stdout
errorlog  = "-"   # stderr
loglevel  = "info"

# Reinicio automático si un worker consume demasiada memoria
max_requests          = 1000
max_requests_jitter   = 100
