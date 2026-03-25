"""
Auto-detect scale connection module.

Provides an alternative connection method that auto-detects serial ports,
reads weight in a single call, and returns structured results using dataclasses.
Does not require a Scale model instance — uses django.conf.settings for config.

Settings (optional, with defaults):
    SCALE_BAUDRATE = 9600
    SCALE_READ_TIMEOUT = 2
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re

import serial
import serial.tools.list_ports
from django.conf import settings


@dataclass
class ScaleReadResult:
    mass: int
    raw_payload: str
    serial_port: str


@dataclass
class ScalePortStatus:
    device: str
    description: str
    hwid: str
    candidate: bool
    available: bool
    error: str = ''


class ScaleReadError(Exception):
    pass


def parse_mass(raw_text):
    """Extract a mass value from raw scale output using multiple strategies."""
    cleaned = raw_text.strip().replace('=', ' ')

    match = re.search(r'(-?\d+(?:\.\d+)?)', cleaned)
    if match:
        return int(Decimal(match.group(1)))

    legacy_slice = cleaned[5:8].strip()
    if re.fullmatch(r'\d+(?:\.\d+)?', legacy_slice):
        try:
            return int(Decimal(legacy_slice))
        except InvalidOperation:
            pass

    for start, stop in ((5, 8), (7, 14)):
        fragment = cleaned[start:stop].strip()
        if not fragment:
            continue
        try:
            return int(Decimal(fragment))
        except InvalidOperation:
            continue

    raise ScaleReadError(f'Unable to parse a mass value from scale payload: {raw_text!r}')


def _is_candidate_port(device):
    """Check if a serial port device name matches known scale port patterns."""
    return (
        device.find('ttyUSB') != -1
        or device.find('serial') != -1
        or device.find('COM') != -1
    )


def inspect_scale_connections():
    """Scan all serial ports and report their status.

    Returns a list of ScalePortStatus for every detected port,
    indicating whether each is a candidate scale port and whether
    it can be opened successfully.
    """
    baudrate = getattr(settings, 'SCALE_BAUDRATE', 9600)
    timeout = 1
    statuses = []

    for comport in serial.tools.list_ports.comports():
        device = comport.device
        candidate = _is_candidate_port(device)
        status = ScalePortStatus(
            device=device,
            description=comport.description or 'n/a',
            hwid=comport.hwid or 'n/a',
            candidate=candidate,
            available=False,
        )

        if candidate:
            try:
                with serial.Serial(device, baudrate=baudrate, timeout=timeout):
                    status.available = True
            except (OSError, serial.SerialException) as exc:
                status.error = str(exc)

        statuses.append(status)

    return statuses


def scale_connect():
    """Auto-detect a scale, connect, read weight, and return a ScaleReadResult.

    Scans all serial ports for candidate devices (ttyUSB, serial, COM),
    connects to the first available one, reads raw bytes, parses mass,
    and returns a ScaleReadResult dataclass.

    Raises ScaleReadError if no port is found or no data is returned.
    """
    scale_connected = False
    ser = None
    last_error = None
    baudrate = getattr(settings, 'SCALE_BAUDRATE', 9600)
    timeout = getattr(settings, 'SCALE_READ_TIMEOUT', 2)

    for comport in serial.tools.list_ports.comports():
        device = comport.device

        if _is_candidate_port(device):
            try:
                ser = serial.Serial(device, baudrate=baudrate, timeout=timeout)
                scale_connected = True
                break
            except (OSError, serial.SerialException) as exc:
                last_error = str(exc)

    if not scale_connected or ser is None:
        raise ScaleReadError(last_error or 'Failed to connect to Serial Port Connection')

    try:
        try:
            ser.reset_input_buffer()
        except (AttributeError, OSError, serial.SerialException):
            pass

        try:
            scale_string = ser.read(32)
            if not scale_string:
                scale_string = ser.readline()
        except (OSError, serial.SerialException) as exc:
            raise ScaleReadError(
                'The scale port became unavailable while reading. '
                'Disconnect any other app using the scale and try again.'
            ) from exc

        if not scale_string:
            raise ScaleReadError(
                'No data was returned from the connected scale. '
                'Check the cable, scale output mode, or port access.'
            )

        raw_text = scale_string.decode('utf-8', errors='ignore').strip()
        mass = parse_mass(raw_text)
        return ScaleReadResult(mass=mass, raw_payload=raw_text, serial_port=ser.port)
    finally:
        try:
            ser.close()
        except (AttributeError, OSError, serial.SerialException):
            pass


def read_scale():
    """Convenience wrapper for scale_connect()."""
    return scale_connect()
