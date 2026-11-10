"""Conservative defaults for a small Render instance; no worker recycling.

Background cloud uploads must not be interrupted by a max_requests limit.
"""
import os

workers = int(os.environ.get('WEB_CONCURRENCY', '1'))
threads = int(os.environ.get('GUNICORN_THREADS', '2'))
worker_class = 'gthread'
timeout = int(os.environ.get('GUNICORN_TIMEOUT', '120'))
keepalive = 5
accesslog = '-'
errorlog = '-'
