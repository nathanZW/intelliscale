from django.apps import AppConfig


class ScaleConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'scale'

    def ready(self):
        import scale.signals
