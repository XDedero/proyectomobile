"""Private, temporary Drive evidence for maintenance reviews."""
from io import BytesIO
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import DatabaseError
from PIL import Image, ImageOps, UnidentifiedImageError

from .tenant_runtime import workspace


MAX_PHOTOS = 3
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
DRIVE_ID = re.compile(r'[A-Za-z0-9_-]{8,128}')


class EvidenceUnavailable(Exception):
    pass


def prepared_images(uploads):
    if len(uploads) > MAX_PHOTOS:
        raise ValidationError('Puedes adjuntar hasta 3 imágenes.')
    images = []
    for upload in uploads:
        if not 0 < upload.size <= MAX_IMAGE_BYTES:
            raise ValidationError('Cada imagen debe pesar hasta 5 MB.')
        try:
            with Image.open(upload, formats=['JPEG', 'PNG', 'WEBP']) as source:
                if source.width * source.height > MAX_IMAGE_PIXELS:
                    raise ValidationError('La imagen supera el tamaño permitido.')
                image = ImageOps.exif_transpose(source)
                image.thumbnail((1600, 1600))
                output = BytesIO()
                image.convert('RGB').save(output, format='JPEG', quality=85)
                images.append(output.getvalue())
        except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError, SyntaxError) as exc:
            raise ValidationError('Adjunta imágenes válidas en formato JPG, PNG o WebP.') from exc
    return images


def drive_configuration():
    company = workspace.get()
    if company:
        return company.drive['credentials'], company.drive['folders']['originals']

    if settings.PLANOS_CLOUD_MODE:
        from .drive_credentials import registered_credentials
        from .models import RELONCAVI_COMPANY_ID

        registered = registered_credentials(RELONCAVI_COMPANY_ID)
        if registered:
            return registered, None

    keys = ('client_id', 'client_secret', 'refresh_token')
    try:
        raw = json.loads((settings.BASE_DIR / 'gdrive-token.json').read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        raw = {}
    credentials = {key: raw.get(key) or os.environ.get('GOOGLE_DRIVE_' + key.upper(), '') for key in keys}
    return credentials, None


def call(url, *, token=None, data=None, content_type=None, method='GET', gone_ok=False):
    headers = {}
    if content_type:
        headers['Content-Type'] = content_type
    if token:
        headers['Authorization'] = 'Bearer ' + token
    try:
        with urlopen(Request(url, data=data, method=method, headers=headers), timeout=15) as response:
            result = response.read(MAX_IMAGE_BYTES + 1)
            if len(result) > MAX_IMAGE_BYTES:
                raise EvidenceUnavailable('La respuesta de Drive supera el tamaño permitido.')
            return result
    except HTTPError as exc:
        exc.close()
        if gone_ok and exc.code == 404:
            return b''
        raise EvidenceUnavailable('Google Drive no pudo completar la operación de las imágenes.') from None
    except (URLError, TimeoutError, OSError):
        raise EvidenceUnavailable('No se pudo acceder a Google Drive para las imágenes.') from None


class EvidenceDrive:
    def __init__(self):
        try:
            credentials, self.folder = drive_configuration()
            if not all(isinstance(value, str) and value for value in credentials.values()):
                raise ValueError
            data = urlencode({**credentials, 'grant_type': 'refresh_token'}).encode()
            result = json.loads(call('https://oauth2.googleapis.com/token', method='POST', data=data,
                                     content_type='application/x-www-form-urlencoded'))
            self.token = result['access_token']
            if not isinstance(self.token, str) or not self.token:
                raise ValueError
        except (DatabaseError, KeyError, TypeError, ValueError, OSError) as exc:
            raise EvidenceUnavailable('El almacenamiento de imágenes de la empresa no está disponible.') from exc

    def reserve(self, count):
        result = call(f'https://www.googleapis.com/drive/v3/files/generateIds?count={count}&space=drive',
                      token=self.token)
        try:
            ids = json.loads(result)['ids']
            if len(ids) == count and len(set(ids)) == count and all(DRIVE_ID.fullmatch(value) for value in ids):
                return ids
        except (KeyError, ValueError, TypeError):
            pass
        raise EvidenceUnavailable('No se pudieron reservar los archivos en Google Drive.')

    def upload(self, file_id, photo):
        boundary = uuid4().hex
        metadata = {'id': file_id, 'name': f'mantencion-temporal-{uuid4().hex}.jpg', 'mimeType': 'image/jpeg'}
        if self.folder:
            metadata['parents'] = [self.folder]
        body = (f'--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n'.encode()
                + json.dumps(metadata).encode()
                + f'\r\n--{boundary}\r\nContent-Type: image/jpeg\r\n\r\n'.encode()
                + photo + f'\r\n--{boundary}--\r\n'.encode())
        result = call('https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id',
                      token=self.token, data=body, method='POST', content_type=f'multipart/related; boundary={boundary}')
        try:
            if json.loads(result)['id'] == file_id:
                return
        except (KeyError, ValueError, TypeError):
            pass
        raise EvidenceUnavailable('Google Drive no confirmó la imagen.')

    @staticmethod
    def url(file_id):
        if not DRIVE_ID.fullmatch(file_id):
            raise EvidenceUnavailable('La referencia de la imagen no es válida.')
        return f'https://www.googleapis.com/drive/v3/files/{file_id}'

    def download(self, file_id):
        return call(self.url(file_id) + '?alt=media', token=self.token)

    def delete(self, file_id):
        call(self.url(file_id), method='DELETE', token=self.token, gone_ok=True)
