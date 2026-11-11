#!/usr/bin/env bash
set -o errexit -o nounset -o pipefail

# In cloud mode only temporary files are local. SQLite installations keep their
# existing data directory; never replace a database or imported documents.
python scripts/prepare_runtime.py
python manage.py migrate --noinput
python init_admin.py
exec gunicorn proyectomobile.wsgi:application --config gunicorn.conf.py --bind "0.0.0.0:${PORT:-10000}"
