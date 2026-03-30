import json
from types import SimpleNamespace
from unittest.mock import patch

import serial
from django.test import SimpleTestCase

from . import satellite_service
from .cas_scale_probe import CasScaleProbe
from .scale_utils import (
    PROTOCOL_CAS_STREAM,
    PROTOCOL_GENERIC,
    get_scale_protocol,
    open_serial_for_scale,
    parse_weight_from_bytes,
    read_cas_stream_sample,
    read_weight_bytes_for_scale,
    read_weight_from_serial,
)
from .views.scale import _get_satellite_cached_weight


class FakeSerial:
    def __init__(
        self,
        initial_buffer=b'',
        responses=None,
        read_failures=0,
        readline_failures=0,
        delayed_buffer=b'',
        delayed_release_after_checks=None,
    ):
        self.timeout = 1
        self.is_open = True
        self._buffer = bytearray(initial_buffer)
        self._responses = responses or {}
        self._read_failures = read_failures
        self._readline_failures = readline_failures
        self._delayed_buffer = delayed_buffer
        self._delayed_release_after_checks = delayed_release_after_checks
        self._in_waiting_checks = 0
        self.writes = []
        self.reset_input_buffer_called = False

    @property
    def in_waiting(self):
        self._in_waiting_checks += 1
        if (
            self._delayed_buffer
            and self._delayed_release_after_checks is not None
            and self._in_waiting_checks > self._delayed_release_after_checks
        ):
            self._buffer.extend(self._delayed_buffer)
            self._delayed_buffer = b''
        return len(self._buffer)

    def read(self, size):
        if self._read_failures:
            self._read_failures -= 1
            raise serial.SerialException(
                'device reports readiness to read but returned no data '
                '(device disconnected or multiple access on port?)'
            )

        chunk = bytes(self._buffer[:size])
        del self._buffer[:size]
        return chunk

    def readline(self):
        if self._readline_failures:
            self._readline_failures -= 1
            raise serial.SerialException(
                'device reports readiness to read but returned no data '
                '(device disconnected or multiple access on port?)'
            )

        lf_pos = self._buffer.find(b'\n')
        cr_pos = self._buffer.find(b'\r')
        if lf_pos != -1:
            end = lf_pos + 1
        elif cr_pos != -1:
            end = cr_pos + 1
        else:
            end = len(self._buffer)

        return self.read(end)

    def write(self, data):
        self.writes.append(data)
        response = self._responses.get(data, b'')
        self._buffer.extend(response)
        return len(data)

    def reset_input_buffer(self):
        self.reset_input_buffer_called = True
        self._buffer.clear()

    def close(self):
        self.is_open = False


class ReadWeightFromSerialTests(SimpleTestCase):
    def test_keeps_existing_buffered_packets(self):
        ser = FakeSerial(initial_buffer=b"    12.5 KG G\r\n")

        result = read_weight_from_serial(ser)

        self.assertEqual(result, b"    12.5 KG G\r\n")
        self.assertFalse(ser.reset_input_buffer_called)
        self.assertEqual(ser.writes, [])

    def test_recovers_from_transient_empty_read(self):
        ser = FakeSerial(initial_buffer=b"    18.0 KG G\r\n", read_failures=1)

        result = read_weight_from_serial(ser)

        self.assertEqual(result, b"    18.0 KG G\r\n")

    def test_recovers_from_transient_empty_readline(self):
        ser = FakeSerial(responses={b"SI\r\n": b"    21.0 KG G\r\n"}, readline_failures=1)

        result = read_weight_from_serial(ser)

        self.assertEqual(result, b"    21.0 KG G\r\n")
        self.assertIn(b"SI\r\n", ser.writes)

    def test_reads_delayed_cas_stream_without_newline(self):
        ser = FakeSerial(
            delayed_buffer=b"= 0004.0= 0004.0",
            delayed_release_after_checks=2,
        )

        result = read_weight_from_serial(ser)

        self.assertEqual(result, b"= 0004.0= 0004.0")
        self.assertFalse(ser.reset_input_buffer_called)


class ParseWeightFromBytesTests(SimpleTestCase):
    def test_parses_live_cas_fixed_width_payload_without_newline(self):
        weight, raw = parse_weight_from_bytes(b'= 0004.0')

        self.assertEqual(weight, 4.0)
        self.assertEqual(raw, '= 0004.0')

    def test_parses_concatenated_cas_packets(self):
        weight, raw = parse_weight_from_bytes(b'= 0004.0= 0004.0')

        self.assertEqual(weight, 4.0)
        self.assertEqual(raw, '= 0004.0')

    def test_parses_signed_zero_cas_packets(self):
        weight, raw = parse_weight_from_bytes(b'=-0000.0=-0000.0')

        self.assertEqual(weight, 0.0)
        self.assertEqual(raw, '=-0000.0')

    def test_rejects_numeric_fragment_without_cas_prefix(self):
        weight, raw = parse_weight_from_bytes(b'    88')

        self.assertIsNone(weight)
        self.assertEqual(raw, '88')


class OpenSerialForScaleTests(SimpleTestCase):
    def test_uses_configured_serial_parameters_before_fallback(self):
        scale = SimpleNamespace(
            com_port='/dev/ttyUSB0',
            baud_rate=4800,
            timeout=3,
            parity='E',
            stop_bits=2,
            data_bits=7,
        )

        with patch('scale.scale_utils.serial.Serial') as serial_ctor:
            serial_ctor.return_value = object()

            open_serial_for_scale(scale, timeout=5)

        serial_ctor.assert_called_once_with(
            port='/dev/ttyUSB0',
            baudrate=4800,
            timeout=5,
            parity='E',
            stopbits=2,
            bytesize=7,
        )

    def test_falls_back_to_pyserial_defaults_if_configured_open_fails(self):
        scale = SimpleNamespace(
            com_port='/dev/ttyUSB0',
            baud_rate=4800,
            timeout=3,
            parity='E',
            stop_bits=2,
            data_bits=7,
        )

        with patch('scale.scale_utils.serial.Serial') as serial_ctor:
            serial_ctor.side_effect = [
                serial.SerialException('bad parity'),
                object(),
            ]

            open_serial_for_scale(scale)

        self.assertEqual(serial_ctor.call_count, 2)
        self.assertEqual(
            serial_ctor.call_args_list[1].kwargs,
            {
                'port': '/dev/ttyUSB0',
                'timeout': 3,
            },
        )


class ScaleProtocolDispatchTests(SimpleTestCase):
    def test_uses_protocol_field_when_present(self):
        scale = SimpleNamespace(protocol=PROTOCOL_CAS_STREAM, mettler_toledo=False)

        self.assertEqual(get_scale_protocol(scale), PROTOCOL_CAS_STREAM)

    def test_falls_back_to_legacy_mettler_checkbox(self):
        scale = SimpleNamespace(mettler_toledo=True)

        self.assertEqual(get_scale_protocol(scale), 'mettler_toledo')

    def test_generic_reader_remains_default(self):
        scale = SimpleNamespace(protocol=PROTOCOL_GENERIC)
        ser = FakeSerial(initial_buffer=b"    12.5 KG G\r\n")

        result = read_weight_bytes_for_scale(scale, ser)

        self.assertEqual(result, b"    12.5 KG G\r\n")

    def test_cas_protocol_uses_bounded_stream_reader(self):
        scale = SimpleNamespace(protocol=PROTOCOL_CAS_STREAM)
        ser = FakeSerial(initial_buffer=b'= 0000.0= 0000.0')

        result = read_weight_bytes_for_scale(scale, ser)

        self.assertEqual(result, b'= 0000.0= 0000.0')


class SatelliteServiceTests(SimpleTestCase):
    def tearDown(self):
        satellite_service.PERSISTENT_SERIAL_READERS.clear()

    def test_reuses_persistent_reader_between_polls(self):
        scale = SimpleNamespace(
            pk=1,
            name='Scale 1',
            scale_id='SCALE1',
            com_port='/dev/ttyUSB0',
            baud_rate=9600,
            timeout=1,
            parity='N',
            stop_bits=1,
            data_bits=8,
        )
        serial_handle = FakeSerial()

        with patch('scale.satellite_service._port_is_available', return_value=True), \
             patch('scale.satellite_service.open_serial_for_scale', return_value=serial_handle) as open_mock, \
             patch('scale.satellite_service.read_weight_bytes_for_scale', return_value=b'= 0004.5'):
            first = satellite_service._read_weight_from_scale(scale)
            second = satellite_service._read_weight_from_scale(scale)

        self.assertEqual(first, (4.5, 'kg', None))
        self.assertEqual(second, (4.5, 'kg', None))
        self.assertEqual(open_mock.call_count, 1)

    def test_recovers_by_reopening_reader_after_serial_exception(self):
        scale = SimpleNamespace(
            pk=1,
            name='Scale 1',
            scale_id='SCALE1',
            com_port='/dev/ttyUSB0',
            baud_rate=9600,
            timeout=1,
            parity='N',
            stop_bits=1,
            data_bits=8,
        )
        first_handle = FakeSerial()
        second_handle = FakeSerial()

        with patch('scale.satellite_service._port_is_available', return_value=True), \
             patch('scale.satellite_service.open_serial_for_scale', side_effect=[first_handle, second_handle]) as open_mock, \
             patch('scale.satellite_service.read_weight_bytes_for_scale', side_effect=[
                 serial.SerialException('Input/output error'),
                 b'= 0004.5',
             ]), \
             patch('scale.satellite_service.time.sleep'):
            result = satellite_service._read_weight_from_scale(scale)

        self.assertEqual(result, (4.5, 'kg', None))
        self.assertEqual(open_mock.call_count, 2)

    def test_marks_cache_entry_as_recovering_on_read_failure(self):
        scale = SimpleNamespace(
            pk=1,
            name='Scale 1',
            scale_id='SCALE1',
            com_port='/dev/ttyUSB0',
            is_active=True,
        )
        written_cache = {}

        def capture_cache(data):
            written_cache.update(data)

        with patch('scale.models.Scale.objects.filter', return_value=[scale]), \
             patch('scale.satellite_service.read_cached_weights', return_value={
                 'SCALE1': {
                     'weight': 14.5,
                     'timestamp': 1_000_000.0,
                     'scale_name': 'Scale 1',
                     'scale_id': 'SCALE1',
                 }
             }), \
             patch('scale.satellite_service._read_weight_from_scale', return_value=(None, None, 'Input/output error')), \
             patch('scale.satellite_service._write_cache', side_effect=capture_cache), \
             patch('scale.satellite_service.time.time', return_value=1_000_010.0):
            satellite_service.poll_all_scales()

        self.assertEqual(written_cache['SCALE1']['weight'], 14.5)
        self.assertEqual(written_cache['SCALE1']['timestamp'], 1_000_000.0)
        self.assertEqual(written_cache['SCALE1']['status'], 'recovering')
        self.assertEqual(written_cache['SCALE1']['last_attempt'], 1_000_010.0)
        self.assertEqual(written_cache['SCALE1']['last_error'], 'Input/output error')


class CasScaleProbeTests(SimpleTestCase):
    def test_read_cas_stream_sample_keeps_zero_weight_packets(self):
        ser = FakeSerial(initial_buffer=b'= 0000.0= 0000.0')

        result = read_cas_stream_sample(ser, max_wait=0.05, idle_window=0.01)

        weight, raw = parse_weight_from_bytes(result)
        self.assertEqual(result, b'= 0000.0= 0000.0')
        self.assertEqual(weight, 0.0)
        self.assertEqual(raw, '= 0000.0')

    def test_probe_marks_empty_reads_without_writing_prompts(self):
        scale = SimpleNamespace(
            pk=1,
            name='Scale 1',
            scale_id='0021',
            com_port='/dev/ttyUSB0',
            baud_rate=9600,
            timeout=1,
            parity='N',
            stop_bits=1,
            data_bits=8,
        )
        probe = CasScaleProbe(scale, read_timeout=0.02, max_wait=0.02, idle_window=0.01)
        probe._serial = FakeSerial(initial_buffer=b'')

        with patch('scale.cas_scale_probe.os.path.exists', return_value=True):
            entry = probe.poll_once(previous_entry={'weight': 0.0, 'timestamp': 1_000_000.0})

        self.assertEqual(entry['status'], 'no_data')
        self.assertEqual(entry['weight'], 0.0)
        self.assertEqual(entry['timestamp'], 1_000_000.0)
        self.assertEqual(probe._serial.writes, [])


class SatelliteCacheViewTests(SimpleTestCase):
    def test_returns_cached_weight_when_satellite_enabled(self):
        scale = SimpleNamespace(pk=1, scale_id='SCALE1', name='Scale 1')

        with patch('scale.models.CompanySettings.objects.first', return_value=SimpleNamespace(satellite=True)), \
             patch('scale.satellite_service.get_cached_weight_by_scale_id', return_value={
                 'weight': 14.5,
                 'unit': 'kg',
                 'timestamp': 1_000_000.0,
                 'scale_id': 'SCALE1',
                 'scale_name': 'Scale 1',
             }), \
             patch('scale.views.scale.time.time', return_value=1_000_001.25):
            response = _get_satellite_cached_weight(scale, tare_weight=2.0)

        payload = json.loads(response.content)
        self.assertTrue(payload['success'])
        self.assertEqual(payload['gross_weight'], 14.5)
        self.assertEqual(payload['weight'], 12.5)
        self.assertEqual(payload['source'], 'satellite_cache')

    def test_returns_recovering_status_when_cache_has_no_weight_yet(self):
        scale = SimpleNamespace(pk=1, scale_id='SCALE1', name='Scale 1')

        with patch('scale.models.CompanySettings.objects.first', return_value=SimpleNamespace(satellite=True)), \
             patch('scale.satellite_service.get_cached_weight_by_scale_id', return_value={
                 'status': 'recovering',
                 'last_attempt': 1_000_001.0,
                 'last_error': 'Input/output error',
                 'scale_id': 'SCALE1',
                 'scale_name': 'Scale 1',
             }):
            response = _get_satellite_cached_weight(scale)

        payload = json.loads(response.content)
        self.assertFalse(payload['success'])
        self.assertEqual(payload['status'], 'recovering')
        self.assertEqual(payload['last_error'], 'Input/output error')
