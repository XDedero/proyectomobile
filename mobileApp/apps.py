from django.apps import AppConfig


class MobileappConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'mobileApp'

    def ready(self):
        from . import terminal_monitor  # noqa: F401
