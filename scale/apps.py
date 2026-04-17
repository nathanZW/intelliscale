from django.apps import AppConfig


class ScaleConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'scale'

    def ready(self):
        import scale.signals
        from core.instrumentation import install_runtime_instrumentation

        install_runtime_instrumentation()
