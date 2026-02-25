from django.apps import AppConfig


class ScaleConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'scale'

    def ready(self):
        import scale.signals

        # Start satellite service if enabled (deferred to avoid issues during migrations)
        import threading
        import os

        # Only start in the main process (not in Django's auto-reloader child check)
        # RUN_MAIN is set to 'true' by the reloader in the child process
        if os.environ.get('RUN_MAIN') == 'true' or 'runserver' not in ''.join(os.sys.argv):
            def _deferred_start():
                """Wait briefly for DB to be ready, then start satellite if enabled."""
                import time
                time.sleep(2)
                try:
                    from scale.satellite_service import start_if_enabled
                    start_if_enabled()
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning(f"Could not start satellite service: {e}")

            t = threading.Thread(target=_deferred_start, daemon=True)
            t.start()
