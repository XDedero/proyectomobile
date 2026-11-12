"""Private Django FileFields backed by Drive, independent of Render's filesystem."""
from pathlib import Path, PurePosixPath
from hashlib import sha256
import os
import re
import shutil
from tempfile import NamedTemporaryFile, TemporaryDirectory, TemporaryFile

from django.conf import settings
from django.core.files import File
from django.core.files.storage import Storage
from django.utils.deconstruct import deconstructible


@deconstructible
class GoogleDriveStorage(Storage):
    def _cache_path(self, name):
        return Path(settings.MEDIA_ROOT) / '.drive-cache' / sha256(name.encode()).hexdigest()

    def _cache(self, name, source):
        """Disposable, bounded disk cache. A cache failure never loses a save."""
        limit = getattr(settings, 'PLANOS_FILE_CACHE_BYTES', 128 * 1024 * 1024)
        if not limit or source.stat().st_size > limit:
            return
        target = self._cache_path(name)
        temporary = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(dir=target.parent, prefix='.write-', delete=False) as stream:
                temporary = Path(stream.name)
                with source.open('rb') as original:
                    shutil.copyfileobj(original, stream)
            os.replace(temporary, target)
            entries = [(path.stat().st_mtime, path, path.stat().st_size)
                       for path in target.parent.iterdir() if not path.name.startswith('.')]
            total = sum(size for _, _, size in entries)
            for _, path, size in sorted(entries):
                if total <= limit:
                    break
                path.unlink(missing_ok=True)
                total -= size
        except OSError:
            pass
        finally:
            if temporary:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

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
            stored = f"gdrive/{result['file']['id']}/{PurePosixPath(name).name}"
            self._cache(stored, source)
        return stored

    def _open(self, name, mode='rb'):
        if mode != 'rb':
            raise ValueError('Los originales de Drive solo se abren en modo lectura binaria.')
        identifier = self.identifier(name)
        cached = self._cache_path(name)
        if getattr(settings, 'PLANOS_FILE_CACHE_BYTES', 128 * 1024 * 1024):
            try:
                os.utime(cached, None)
                return File(cached.open('rb'), name=name)
            except OSError:
                pass
        with TemporaryDirectory(prefix='reloncavi-open-') as temporary:
            destination = Path(temporary) / 'source'
            self.command('--open', '--id', identifier, '--output', str(destination))
            self._cache(name, destination)
            # The response owns this temporary handle; closing it removes the
            # file. Never materialize an entire download as a Python buffer.
            stream = TemporaryFile(mode='w+b')
            try:
                with destination.open('rb') as original:
                    shutil.copyfileobj(original, stream)
                stream.seek(0)
                return File(stream, name=name)
            except Exception:
                stream.close()
                raise

    def delete(self, name):
        try:
            self.command('--delete', '--id', self.identifier(name))
        except FileNotFoundError:
            pass
        try:
            self._cache_path(name).unlink(missing_ok=True)
        except OSError:
            pass

    def exists(self, name):
        try:
            self.command('--stat', '--id', self.identifier(name))
            return True
        except FileNotFoundError:
            return False

    def size(self, name):
        return int(self.command('--stat', '--id', self.identifier(name))['file']['size'])
