#!/usr/bin/env bash
set -o errexit -o nounset -o pipefail

# The persistent disk is mounted now. Never replace an existing database or media.
python -c 'import os; os.environ.setdefault("DJANGO_SETTINGS_MODULE", "proyectomobile.settings"); from django.conf import settings; settings.APP_DATA_DIR.mkdir(parents=True, exist_ok=True); settings.MEDIA_ROOT.mkdir(parents=True, exist_ok=True)'
python manage.py migrate --noinput
python init_admin.py
exec gunicorn proyectomobile.wsgi:application --config gunicorn.conf.py --bind "0.0.0.0:${PORT:-10000}"
