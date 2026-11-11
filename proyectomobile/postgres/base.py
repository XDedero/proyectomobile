"""Keep Django tables private without pooler startup options."""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.backends.postgresql.base import DatabaseWrapper as PostgreSQLDatabaseWrapper
from psycopg import sql


class DatabaseWrapper(PostgreSQLDatabaseWrapper):
    def init_connection_state(self):
        super().init_connection_state()
        with self.connection.cursor() as cursor:
            cursor.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(settings.PLANOS_DATABASE_SCHEMA)))
            cursor.execute('SELECT current_schema()')
            if cursor.fetchone()[0] != settings.PLANOS_DATABASE_SCHEMA:
                raise ImproperlyConfigured('Prepare el esquema privado ejecutando python scripts/prepare_runtime.py antes de migrate.')
        if not self.get_autocommit():
            self.connection.commit()
