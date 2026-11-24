"""Read central Drive authorizations without exposing them to the browser."""
import json

from django.conf import settings
from django.db import connection


def credentials_payload(raw, identity, expected):
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get(identity) != str(expected):
        raise ValueError('La autorización no corresponde a la cuenta solicitada.')
    credentials = {key: payload[key] for key in ('client_id', 'client_secret', 'refresh_token')}
    if not all(isinstance(value, str) and value.strip() for value in credentials.values()):
        raise ValueError('La autorización de almacenamiento está incompleta.')
    return credentials


def registered_credentials(company_id, account_id=None):
    """Always use the central schema, even inside a tenant's search_path.

    None means the account has not been migrated. A broken or mismatched
    registered secret raises instead of falling back to an older authorization.
    """
    from psycopg import sql
    with connection.cursor() as cursor:
        cursor.execute(sql.SQL('''
            SELECT a.id, a.account_id, s.decrypted_secret
            FROM {} a LEFT JOIN vault.decrypted_secrets s
              ON s.id=a.secret_id AND s.name='planos_drive_account_' || replace(a.id::text, '-', '')
            WHERE a.company_id=%s
        ''').format(sql.Identifier(settings.PLANOS_DATABASE_SCHEMA, 'mobileApp_drivestorageaccount')), [company_id])
        row = cursor.fetchone()
    if row is None:
        return None
    if account_id is not None and row[1] != account_id:
        raise ValueError('La cuenta de almacenamiento no coincide con la empresa.')
    if row[2] is None:
        raise ValueError('No está disponible la autorización de almacenamiento.')
    return credentials_payload(row[2], 'account_id', row[1])
