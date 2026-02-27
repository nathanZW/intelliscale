"""
Management command to run the satellite weight polling service.
Runs as a standalone process, separate from gunicorn.

Usage:
    python manage.py run_satellite
    python manage.py run_satellite --interval 0.5
"""
from django.core.management.base import BaseCommand
from scale.models import CompanySettings
from scale.satellite_service import run_satellite_loop


class Command(BaseCommand):
    help = 'Run the satellite weight polling service (background process)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--interval',
            type=float,
            default=1.0,
            help='Polling interval in seconds (default: 1.0)',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Start even if satellite is not enabled in Company Settings',
        )

    def handle(self, *args, **options):
        interval = options['interval']
        force = options['force']

        # Check if satellite mode is enabled
        if not force:
            settings = CompanySettings.objects.first()
            if not settings or not settings.satellite:
                self.stdout.write(self.style.WARNING(
                    'Satellite mode is not enabled in Company Settings. '
                    'Use --force to start anyway.'
                ))
                return

        self.stdout.write(self.style.SUCCESS(
            f'Starting satellite service (polling every {interval}s)...'
        ))

        try:
            run_satellite_loop(poll_interval=interval)
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING('\nSatellite service stopped.'))
