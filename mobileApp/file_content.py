"""Binary Python interface, private immutable files, and small SQL references.

Model reads stay lazy: selecting a row never downloads its file. ``content``
returns bytes on first access; values()/values_list() expose the stored name.
All writes, including conditional QuerySet.update(), store the file before SQL.
"""
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath
from uuid import uuid4

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import models
from django.db.models.query_utils import DeferredAttribute


MAX_CONTENT_BYTES = 100 * 1024 * 1024


def write_content(content, extension):
    data = bytes(content)
    if len(data) > MAX_CONTENT_BYTES:
        raise ValueError('El archivo supera el límite de 100 MB.')
    if not data:
        return ''
    # A new reference never overwrites bytes used by another revision/transaction.
    name = f'documents/{uuid4().hex}/{sha256(data).hexdigest()}.{extension}'
    return default_storage.save(name, ContentFile(data))


def open_content(name):
    """Verify in bounded chunks, then let FileResponse stream the private file."""
    if not name:
        return BytesIO()
    stream = default_storage.open(name, 'rb')
    try:
        checksum = sha256()
        size = 0
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_CONTENT_BYTES:
                raise OSError('El archivo supera el límite de 100 MB.')
            checksum.update(chunk)
        digest = PurePosixPath(name).stem
        if len(digest) != 64 or checksum.hexdigest() != digest:
            raise OSError('La verificación del archivo almacenado no coincide.')
        stream.seek(0)
        return stream
    except Exception:
        stream.close()
        raise


def read_content(name):
    with open_content(name) as stream:
        return stream.read()


class FileContentDescriptor(DeferredAttribute):
    def __get__(self, instance, cls=None):
        if instance is None:
            return self
        value = super().__get__(instance, cls)
        if not isinstance(value, str):
            return bytes(value or b'')
        cache_key = f'_{self.field.attname}_file_cache'
        cached = instance.__dict__.get(cache_key)
        if cached is None or cached[0] != value:
            cached = (value, read_content(value))
            instance.__dict__[cache_key] = cached
        return cached[1]

    def __set__(self, instance, value):
        key = self.field.attname
        cached = instance.__dict__.get(f'_{key}_file_cache')
        if isinstance(value, (bytes, bytearray, memoryview)) and cached and value == cached[1]:
            value = cached[0]
        else:
            instance.__dict__.pop(f'_{key}_file_cache', None)
        instance.__dict__[key] = value


class FileContentField(models.CharField):
    """Keep the existing byte API without putting XLSX/ZIP data in SQL."""
    descriptor_class = FileContentDescriptor

    def __init__(self, *args, extension, **kwargs):
        self.extension = extension
        kwargs.setdefault('max_length', 255)
        kwargs.setdefault('default', bytes)
        kwargs.setdefault('editable', False)
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs['extension'] = self.extension
        return name, path, args, kwargs

    def to_python(self, value):
        return value

    def get_prep_value(self, value):
        if isinstance(value, (bytes, bytearray, memoryview)):
            return write_content(value, self.extension)
        return value or ''

    def pre_save(self, model_instance, add):
        if self.attname not in model_instance.__dict__:
            getattr(model_instance, self.attname)
        value = model_instance.__dict__[self.attname]
        name = self.get_prep_value(value)
        model_instance.__dict__[self.attname] = name
        if isinstance(value, (bytes, bytearray, memoryview)):
            model_instance.__dict__[f'_{self.attname}_file_cache'] = (name, bytes(value))
        return name

    def value_to_string(self, obj):
        # dumpdata/loaddata transport references, never stringify binary bytes.
        if self.attname not in obj.__dict__:
            getattr(obj, self.attname)
        return self.get_prep_value(obj.__dict__[self.attname])
