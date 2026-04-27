"""
Management command to run the satellite weight polling service.
Runs as a standalone process, separate from gunicorn.

Usage:
    python manage.py run_satellite
    python manage.py run_satellite --interval 0.1
"""
from django.core.management.base import BaseCommand
from scale.satellite_service import run_satellite_loop


class Command(BaseCommand):
    help = 'Run the satellite weight polling service (background process)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--interval',
            type=float,
            default=0.1,
            help='Polling interval in seconds (default: 0.1)',
        )

    def handle(self, *args, **options):
        interval = options['interval']

        self.stdout.write(self.style.SUCCESS(
            f'Starting satellite service (polling every {interval}s)...'
        ))

        try:
            run_satellite_loop(poll_interval=interval)
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING('\nSatellite service stopped.'))
