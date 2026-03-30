"""
Simple CAS scale probe for bounded, stream-only reads.

This avoids the generic prompt/readline fallbacks used elsewhere in the app and
is intended for direct hardware troubleshooting against CAS fixed-width output.
"""
import json
import os
import time
from datetime import datetime, timezone

import serial

from scale.scale_utils import open_serial_for_scale, parse_weight_from_bytes, read_cas_stream_sample


PROBE_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    '.cas_probe_cache.json',
)


def _cache_key_for_scale(scale):
    return str(getattr(scale, 'scale_id', None) or getattr(scale, 'pk', None) or getattr(scale, 'name', 'cas-probe'))


def _isoformat_timestamp(timestamp):
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone().isoformat()


def read_probe_cache(cache_file=PROBE_CACHE_FILE):
    try:
        if os.path.exists(cache_file):
            with open(cache_file, 'r') as handle:
                return json.load(handle)
    except (json.JSONDecodeError, OSError, IOError):
        pass
    return {}


def write_probe_cache(data, cache_file=PROBE_CACHE_FILE):
    tmp_file = cache_file + '.tmp'
    with open(tmp_file, 'w') as handle:
        json.dump(data, handle)
    os.replace(tmp_file, cache_file)


class CasScaleProbe:
    def __init__(
        self,
        scale,
        read_timeout=0.25,
        max_wait=0.25,
        idle_window=0.05,
        exclusive=True,
    ):
        self.scale = scale
        self.read_timeout = read_timeout
        self.max_wait = max_wait
        self.idle_window = idle_window
        self.exclusive = exclusive
        self._serial = None

    @property
    def cache_key(self):
        return _cache_key_for_scale(self.scale)

    def close(self):
        if self._serial and getattr(self._serial, 'is_open', False):
            try:
                self._serial.close()
            except Exception:
                pass
        self._serial = None

    def _port_is_available(self):
        return bool(getattr(self.scale, 'com_port', None)) and os.path.exists(self.scale.com_port)

    def _ensure_serial(self):
        if self._serial and getattr(self._serial, 'is_open', False):
            return self._serial

        self.close()
        self._serial = open_serial_for_scale(
            self.scale,
            timeout=self.read_timeout,
            exclusive=self.exclusive,
        )
        return self._serial

    def _base_entry(self, previous_entry, attempt_at):
        entry = dict(previous_entry or {})
        entry.update(
            {
                'scale_name': self.scale.name,
                'scale_id': str(getattr(self.scale, 'scale_id', None) or ''),
                'com_port': getattr(self.scale, 'com_port', None),
                'status': 'pending',
                'last_attempt': attempt_at,
                'last_attempt_iso': _isoformat_timestamp(attempt_at),
            }
        )
        entry.pop('last_error', None)
        return entry

    def poll_once(self, previous_entry=None):
        attempt_at = time.time()
        entry = self._base_entry(previous_entry, attempt_at)

        if not self._port_is_available():
            self.close()
            entry['status'] = 'no_port'
            entry['last_error'] = f"Port unavailable: {getattr(self.scale, 'com_port', None)}"
            entry.pop('raw_packet', None)
            entry.pop('raw_bytes_hex', None)
            return entry

        try:
            raw_bytes = read_cas_stream_sample(
                self._ensure_serial(),
                max_wait=self.max_wait,
                idle_window=self.idle_window,
            )
        except (serial.SerialException, OSError, ValueError) as exc:
            self.close()
            entry['status'] = 'serial_error'
            entry['last_error'] = str(exc)
            entry.pop('raw_packet', None)
            entry.pop('raw_bytes_hex', None)
            return entry

        if not raw_bytes:
            entry['status'] = 'no_data'
            entry.pop('raw_packet', None)
            entry.pop('raw_bytes_hex', None)
            return entry

        weight, raw_packet = parse_weight_from_bytes(raw_bytes)
        entry['raw_packet'] = raw_packet
        entry['raw_bytes_hex'] = raw_bytes.hex()
        entry['bytes_captured'] = len(raw_bytes)

        if weight is None:
            entry['status'] = 'unparsed'
            entry['last_error'] = f'Could not parse CAS payload: {raw_packet or raw_bytes!r}'
            return entry

        entry.update(
            {
                'weight': weight,
                'unit': 'kg',
                'timestamp': attempt_at,
                'timestamp_iso': _isoformat_timestamp(attempt_at),
                'status': 'ok',
            }
        )
        return entry
