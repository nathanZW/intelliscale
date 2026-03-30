"""
Management command for a simple CAS-specific serial probe.

This is intentionally narrower than the main satellite service: it uses a
single persistent connection and bounded stream reads only, so it is easier to
observe the raw device behavior without the app's generic fallbacks.
"""
import json
import time
from types import SimpleNamespace

from django.core.management.base import BaseCommand, CommandError

from scale.cas_scale_probe import CasScaleProbe, PROBE_CACHE_FILE, read_probe_cache, write_probe_cache
from scale.models import Scale


class Command(BaseCommand):
    help = 'Run a simple CAS scale probe with satellite-style JSON output'

    def add_arguments(self, parser):
        parser.add_argument('--scale-id', help='Scale.scale_id to probe (defaults to the first active scale)')
        parser.add_argument('--db-id', type=int, help='Scale primary key to probe')
        parser.add_argument('--port', help='Serial port path, for example /dev/ttyUSB0')
        parser.add_argument('--name', default='CAS Probe', help='Display name when using --port without a DB scale')
        parser.add_argument('--baud-rate', type=int, default=9600, help='Baud rate when using --port directly')
        parser.add_argument('--parity', default='N', help='Parity when using --port directly')
        parser.add_argument('--stop-bits', type=int, default=1, help='Stop bits when using --port directly')
        parser.add_argument('--data-bits', type=int, default=8, help='Data bits when using --port directly')
        parser.add_argument('--interval', type=float, default=0.5, help='Polling interval in seconds')
        parser.add_argument('--samples', type=int, default=0, help='Number of samples to capture before exiting (0 = run forever)')
        parser.add_argument('--read-timeout', type=float, default=0.25, help='Per-read serial timeout in seconds')
        parser.add_argument('--max-wait', type=float, default=0.25, help='Maximum time spent waiting for a sample')
        parser.add_argument('--idle-window', type=float, default=0.05, help='How long the stream must go idle before a sample is returned')
        parser.add_argument('--cache-file', default=PROBE_CACHE_FILE, help='JSON cache path to write')
        parser.add_argument('--no-exclusive', action='store_true', help='Disable exclusive serial open mode')

    def handle(self, *args, **options):
        scale = self._resolve_scale(options)
        probe = CasScaleProbe(
            scale=scale,
            read_timeout=options['read_timeout'],
            max_wait=options['max_wait'],
            idle_window=options['idle_window'],
            exclusive=not options['no_exclusive'],
        )
        cache_file = options['cache_file']
        interval = options['interval']
        samples = options['samples']
        sample_count = 0

        self.stdout.write(
            self.style.SUCCESS(
                f"Starting CAS probe for {scale.name} on {scale.com_port} "
                f"(interval={interval}s, read_timeout={options['read_timeout']}s, cache={cache_file})"
            )
        )
        self.stdout.write('Stop the main satellite process first if you want this probe to be the only serial owner.')

        try:
            while True:
                cache = read_probe_cache(cache_file)
                entry = probe.poll_once(previous_entry=cache.get(probe.cache_key))
                cache[probe.cache_key] = entry
                write_probe_cache(cache, cache_file=cache_file)
                self.stdout.write(json.dumps({probe.cache_key: entry}, sort_keys=True))

                sample_count += 1
                if samples and sample_count >= samples:
                    break

                time.sleep(interval)
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING('\nCAS probe stopped.'))
        finally:
            probe.close()

    def _resolve_scale(self, options):
        if options['port']:
            return SimpleNamespace(
                pk=None,
                name=options['name'],
                scale_id=options.get('scale_id'),
                com_port=options['port'],
                baud_rate=options['baud_rate'],
                timeout=options['read_timeout'],
                parity=options['parity'],
                stop_bits=options['stop_bits'],
                data_bits=options['data_bits'],
            )

        queryset = Scale.objects.filter(is_active=True)
        if options['db_id'] is not None:
            scale = queryset.filter(pk=options['db_id']).first()
            if not scale:
                raise CommandError(f"No active scale found with db id {options['db_id']}.")
            return scale

        if options['scale_id']:
            scale = queryset.filter(scale_id=options['scale_id']).first()
            if not scale:
                raise CommandError(f"No active scale found with scale_id {options['scale_id']}.")
            return scale

        scale = queryset.order_by('pk').first()
        if not scale:
            raise CommandError('No active scales found. Pass --port to probe a serial device directly.')
        return scale
