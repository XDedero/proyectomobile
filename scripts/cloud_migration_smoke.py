"""Verify a copy of local data using isolated databases and an in-memory Drive.

No network, no migration of the real database and no changes to original media.
"""
from copy import deepcopy
from contextlib import closing
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))


class MemoryDrive:
    def __init__(self):
        self.files={}
        self.uploads=0

    def save(self,name,content):
        self.uploads+=1
        target=f'gdrive/test-{self.uploads}/{Path(name).name}'
        self.files[target]=content.read()
        return target

    def open(self,name,mode='rb'):
        if name not in self.files:
            raise FileNotFoundError(name)
        return BytesIO(self.files[name])

    def delete(self,name):
        self.files.pop(name,None)


def main():
    source=ROOT/'db.sqlite3'
    media=ROOT/'media'
    original_hash=sha256(source.read_bytes()).hexdigest()
    media_hashes={p:sha256(p.read_bytes()).hexdigest() for p in media.rglob('*') if p.is_file()}
    with TemporaryDirectory(prefix='reloncavi-transfer-check-') as directory:
        temporary=Path(directory)
        snapshot=temporary/'source.sqlite3'
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as original:
            with closing(sqlite3.connect(snapshot)) as backup:
                original.backup(backup)
        os.environ.update(DJANGO_SETTINGS_MODULE='proyectomobile.settings',PLANOS_CLOUD_MODE='0',
                          PLANOS_DATA_DIR=str(temporary),APP_SHARED_ACCOUNTS='0',APP_TERMINAL_DEBUG='0')
        sys.argv.append('test')
        import django
        django.setup()
        from django.apps import apps
        from django.contrib.auth import get_user_model
        from django.core.files.storage import default_storage
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from django.db import connections
        from mobileApp.management.commands.migrate_local_to_cloud import copy_installation, installation_models
        from mobileApp.project_service import open_plan_source
        alias='local_cloud_transfer'
        config=deepcopy(connections.databases['default'])
        config.update(NAME=snapshot.as_uri()+'?mode=ro',OPTIONS={'uri':True})
        connections.databases[alias]=config
        try:
            call_command('migrate',verbosity=0)
            drive=MemoryDrive()
            default_storage._wrapped=drive
            result=copy_installation(alias,media.resolve(),drive)
            for model in installation_models():
                assert model.objects.count()==model.objects.using(alias).count(),model._meta.label
                binary=[field for field in model._meta.fields if field.get_internal_type()=='BinaryField']
                for original in model.objects.using(alias).all():
                    copied=model.objects.get(pk=original.pk)
                    for field in binary:
                        assert bytes(getattr(copied,field.name))==bytes(getattr(original,field.name)),field.name
            User=get_user_model()
            for original in User.objects.using(alias).all():
                copied=User.objects.get(pk=original.pk)
                assert copied.password==original.password
                assert copied.get_all_permissions()==original.get_all_permissions()
            for model,kind in [(apps.get_model('mobileApp','PdfPlan'),'pdf'),(apps.get_model('mobileApp','Plan'),'dxf')]:
                for plan in model.objects.all():
                    with open_plan_source(kind,plan) as content:
                        assert content.read()==drive.files[plan.source.name]
            uploads=drive.uploads
            try:
                copy_installation(alias,media.resolve(),drive)
                raise AssertionError('A populated target must be rejected')
            except CommandError:
                pass
            assert drive.uploads==uploads
            print(f"PASS: copied {result['records']} records and {result['files']} originals; passwords, permissions and binary content match")
            print('PASS: repeat import is refused before any upload')
        finally:
            connections.close_all()
            del connections[alias]
            del connections.databases[alias]
    assert sha256(source.read_bytes()).hexdigest()==original_hash
    assert all(sha256(path.read_bytes()).hexdigest()==digest for path,digest in media_hashes.items())
    print('PASS: original SQLite and media are unchanged; no cloud services were contacted')


if __name__=='__main__':
    main()
