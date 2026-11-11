"""Private Django FileFields backed by Drive, independent of Render's filesystem."""
from pathlib import Path, PurePosixPath
import re
from tempfile import TemporaryDirectory

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible


@deconstructible
class GoogleDriveStorage(Storage):
    def command(self, *args):
        from .cloud_projects import command_json
        result = command_json(
            ['node', str(settings.BASE_DIR / 'scripts/drive-storage.js'), *args],
            'No se pudo acceder al archivo privado en Google Drive.', timeout=120,
        )
        if not result.get('ok'):
            if result.get('missing'):
                raise FileNotFoundError('El archivo ya no esta en Drive.')
            raise OSError('No se pudo completar la operacion de almacenamiento en Drive.')
        return result

    @staticmethod
    def identifier(name):
        match = re.fullmatch(r'gdrive/([A-Za-z0-9_-]{1,128})/[^/]+', name)
        if not match:
            raise FileNotFoundError('El archivo no tiene una referencia valida de Drive. Complete la migracion de media.')
        return match[1]

    def get_available_name(self, name, max_length=None):
        # Uploaded originals always receive a new immutable Drive ID.
        return name

    def _save(self, name, content):
        with TemporaryDirectory(prefix='reloncavi-save-') as temporary:
            source = Path(temporary) / 'source'
            with source.open('wb') as stream:
                for chunk in content.chunks():
                    stream.write(chunk)
            result = self.command('--save', '--file', str(source), '--name', name)
        return f"gdrive/{result['file']['id']}/{PurePosixPath(name).name}"

    def _open(self, name, mode='rb'):
        if mode != 'rb':
            raise ValueError('Los originales de Drive solo se abren en modo lectura binaria.')
        with TemporaryDirectory(prefix='reloncavi-open-') as temporary:
            destination = Path(temporary) / 'source'
            self.command('--open', '--id', self.identifier(name), '--output', str(destination))
            return ContentFile(destination.read_bytes(), name=name)

    def delete(self, name):
        try:
            self.command('--delete', '--id', self.identifier(name))
        except FileNotFoundError:
            pass

    def exists(self, name):
        try:
            self.command('--stat', '--id', self.identifier(name))
            return True
        except FileNotFoundError:
            return False

    def size(self, name):
        return int(self.command('--stat', '--id', self.identifier(name))['file']['size'])
