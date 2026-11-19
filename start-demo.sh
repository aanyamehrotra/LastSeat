#!/bin/sh
set -eu

flask --app app init-demo
exec gunicorn app:app --bind "0.0.0.0:${PORT:-8000}" --workers 2 --threads 4 --timeout 30
