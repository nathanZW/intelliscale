from types import SimpleNamespace
from unittest.mock import patch

import serial
from django.test import SimpleTestCase

from .scale_utils import open_serial_for_scale, parse_weight_from_bytes, read_weight_from_serial


class FakeSerial:
    def __init__(self, initial_buffer=b'', responses=None, read_failures=0, readline_failures=0):
        self.timeout = 1
        self.is_open = True
        self._buffer = bytearray(initial_buffer)
        self._responses = responses or {}
        self._read_failures = read_failures
        self._readline_failures = readline_failures
        self.writes = []
        self.reset_input_buffer_called = False

    @property
    def in_waiting(self):
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


class ParseWeightFromBytesTests(SimpleTestCase):
    def test_parses_live_cas_fixed_width_payload_without_newline(self):
        weight, raw = parse_weight_from_bytes(b'= 0004.0')

        self.assertEqual(weight, 4.0)
        self.assertEqual(raw, '= 0004.0')

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
