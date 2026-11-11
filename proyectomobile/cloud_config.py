"""Parse private PostgreSQL configuration without exposing credentials in errors."""
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit
import re


def cloud_enabled(environ):
    value = environ.get('PLANOS_CLOUD_MODE', '0').strip().lower()
    if value not in {'0', '1', 'false', 'true', 'no', 'yes', ''}:
        raise ValueError('PLANOS_CLOUD_MODE debe ser 1 para Supabase y Drive, o 0 para una instalacion local.')
    return value in {'1', 'true', 'yes'}


def database_url(environ, root):
    value = environ.get('SUPABASE_DATABASE_URL')
    if not value:
        profile = Path(root) / 'conexion-base-datos.txt'
        if profile.is_file():
            value = profile.read_text(encoding='utf-8-sig')
    return (value or '').strip()


def postgres_database(value):
    message = 'Configure SUPABASE_DATABASE_URL con la URI PostgreSQL real de Supabase (Session pooler).'
    try:
        parsed = urlsplit(value)
        password = unquote(parsed.password or '')
        if (parsed.scheme not in {'postgres', 'postgresql'} or not parsed.hostname
                or not parsed.username or not password or not parsed.path.strip('/')
                or any(char.isspace() for char in value)
                or re.search(r'\[(?:tu[_ -]?password|your[_ -]?password|password)\]|REEMPLAZAR_PASSWORD', password, re.I)):
            raise ValueError
        if parsed.hostname.endswith('.pooler.supabase.com') and parsed.port != 5432:
            raise ValueError
        options = dict(parse_qsl(parsed.query, keep_blank_values=True))
        options.pop('options', None)
        # Session pooler supports normal PostgreSQL connections. Disabling prepared
        # statements also keeps the connection usable with transaction poolers.
        options.setdefault('sslmode', 'require')
        options.setdefault('connect_timeout', 12)
        options['prepare_threshold'] = None
        return {
            'ENGINE': 'proyectomobile.postgres',
            'NAME': unquote(parsed.path[1:]),
            'USER': unquote(parsed.username),
            'PASSWORD': password,
            'HOST': parsed.hostname,
            'PORT': parsed.port or 5432,
            'CONN_MAX_AGE': 0,
            'CONN_HEALTH_CHECKS': True,
            'OPTIONS': options,
        }
    except (ValueError, TypeError):
        raise ValueError(message) from None


def private_schema(environ):
    schema = environ.get('PLANOS_DATABASE_SCHEMA', 'app_runtime')
    if not re.fullmatch(r'[a-z_][a-z0-9_]{0,62}', schema) or schema in {'public', 'auth', 'storage', 'app_identity', 'pg_catalog', 'information_schema'}:
        raise ValueError('PLANOS_DATABASE_SCHEMA debe identificar un esquema privado propio de la aplicacion.')
    return schema


def prepare_schema(config, schema):
    import psycopg
    from psycopg import sql
    with psycopg.connect(dbname=config['NAME'], user=config['USER'], password=config['PASSWORD'],
                         host=config['HOST'], port=config['PORT'], autocommit=True, **config['OPTIONS']) as connection:
        connection.execute(sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(sql.Identifier(schema)))
        connection.execute(sql.SQL('REVOKE ALL ON SCHEMA {} FROM PUBLIC').format(sql.Identifier(schema)))
        for role in ('anon', 'authenticated'):
            if connection.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,)).fetchone():
                connection.execute(sql.SQL('REVOKE ALL ON SCHEMA {} FROM {}').format(sql.Identifier(schema), sql.Identifier(role)))
