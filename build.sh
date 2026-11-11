#!/usr/bin/env bash
set -o errexit -o nounset -o pipefail

pip install -r requirements.txt
python manage.py collectstatic --no-input
# Render disks are mounted at runtime, not during builds. Preserve the legacy
# build flow for installations that still use the repository's data directory.
if [ -z "${PLANOS_DATA_DIR:-}" ] && [ "${PLANOS_CLOUD_MODE:-0}" != "1" ] && [ "${PLANOS_CLOUD_MODE:-0}" != "true" ] && [ "${PLANOS_CLOUD_MODE:-0}" != "yes" ]; then
    python manage.py migrate --noinput
    python init_admin.py
fi
