#!/usr/bin/env bash
set -o errexit -o nounset -o pipefail

pip install -r requirements.txt
python manage.py collectstatic --no-input
# Render disks are mounted at runtime, not during builds. Preserve the legacy
# build flow for installations that still use the repository's data directory.
planos_cloud_mode="${PLANOS_CLOUD_MODE:-0}"
planos_cloud_mode="${planos_cloud_mode,,}"
if [ -z "${PLANOS_DATA_DIR:-}" ] && [ "$planos_cloud_mode" != "1" ] && [ "$planos_cloud_mode" != "true" ] && [ "$planos_cloud_mode" != "yes" ]; then
    python manage.py migrate --noinput
    python init_admin.py
fi
