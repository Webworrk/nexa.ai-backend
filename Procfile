# Procfile
web: gunicorn app:app --worker-class uvicorn.workers.UvicornWorker --workers 4 --bind 0.0.0.0:$PORT --timeout 120 --keep-alive 5 --log-level debug

# render.yaml (if using Render)
services:
  - type: web
    name: nexa-backend
    env: python
    buildCommand: pip install -r requirements.txt
    startCommand: gunicorn app:app --worker-class uvicorn.workers.UvicornWorker --workers 4 --bind 0.0.0.0:$PORT
    envVars:
      - key: PYTHON_VERSION
        value: 3.11.0
      - key: PORT
        value: 8080

# .env
PORT=5000
WEB_CONCURRENCY=4
WORKERS_PER_CORE=1
MAX_WORKERS=4
