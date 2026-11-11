"""Prepare writable temporary files; persistent cloud data lives outside Render."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def prepare(settings, *, root=ROOT, secrets_dir=Path('/etc/secrets')):
    try:
        settings.APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        settings.MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise ValueError(
            'No se puede escribir en PLANOS_DATA_DIR. En Render gratuito use '
            'PLANOS_CLOUD_MODE=1 con Supabase y Drive; /var/data requiere un disco persistente.'
        ) from None
    if not settings.PLANOS_CLOUD_MODE:
        return
    target = root / 'gdrive-token.json'
    if not target.is_file():
        seed = next((candidate for candidate in (
            secrets_dir / 'gdrive-token-seed.json', root / 'gdrive-token-seed.json',
            secrets_dir / 'gdrive-token.json',
        ) if candidate.is_file()), None)
        if seed is None:
            raise ValueError('Agregue el Secret File gdrive-token-seed.json en Render con la autorizacion de Drive.')
        try:
            raw = seed.read_bytes()
            token = json.loads(raw)
            if not isinstance(token, dict) or not token.get('refresh_token'):
                raise ValueError
            with target.open('xb') as stream:
                stream.write(raw)
            target.chmod(0o600)
        except FileExistsError:
            pass
        except (ValueError, OSError):
            raise ValueError('El archivo secreto de Drive no es valido o no se puede preparar.') from None
    try:
        token = json.loads(target.read_text(encoding='utf-8-sig'))
        if not isinstance(token, dict) or not token.get('refresh_token'):
            raise ValueError
        if not (token.get('client_id') or os.environ.get('GOOGLE_DRIVE_CLIENT_ID')):
            raise ValueError
        if not (token.get('client_secret') or os.environ.get('GOOGLE_DRIVE_CLIENT_SECRET')):
            raise ValueError
    except (ValueError, OSError):
        raise ValueError('La autorizacion de Drive debe incluir refresh_token, client_id y client_secret.') from None


def main():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'proyectomobile.settings')
    from django.conf import settings
    try:
        prepare(settings)
        if settings.PLANOS_CLOUD_MODE:
            from proyectomobile.cloud_config import prepare_schema
            try:
                prepare_schema(settings.DATABASES['default'], settings.PLANOS_DATABASE_SCHEMA)
            except Exception:
                print('No se pudo preparar Supabase. Revise SUPABASE_DATABASE_URL, la contrasena y el Session pooler (puerto 5432).', file=sys.stderr)
                return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print('Almacenamiento: Supabase + Drive.' if settings.PLANOS_CLOUD_MODE else 'Almacenamiento local preparado.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
