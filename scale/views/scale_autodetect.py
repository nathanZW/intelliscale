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

import serial
import serial.tools.list_ports
from django.conf import settings

from ..scale_utils import (
    parse_weight_from_bytes,
    read_weight_from_serial,
    open_scale_serial,
    is_candidate_serial_port
)


@dataclass
class ScaleReadResult:
    mass: float
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
    """Extract a mass value from raw scale output using our shared robust parser."""
    # Note: parse_weight_from_bytes expects bytes, so we encode back if needed,
    # or just use it on the raw bytes earlier.
    # For compatibility with existing callers, we'll try to parse the string.
    weight, raw_str = parse_weight_from_bytes(raw_text.encode('utf-8'))
    if weight is not None:
        return weight

    raise ScaleReadError(f'Unable to parse a mass value from scale payload: {raw_text!r}')


def inspect_scale_connections():
    """Scan all serial ports and report their status.

    Returns a list of ScalePortStatus for every detected port,
    indicating whether each is a candidate scale port and whether
    it can be opened successfully.
    """
    baudrate = getattr(settings, 'SCALE_BAUDRATE', 9600)
    statuses = []

    for comport in serial.tools.list_ports.comports():
        device = comport.device
        candidate = is_candidate_serial_port(device)
        status = ScalePortStatus(
            device=device,
            description=comport.description or 'n/a',
            hwid=comport.hwid or 'n/a',
            candidate=candidate,
            available=False,
        )

        if candidate:
            ser = None
            try:
                # Use our robust opener which disables DTR/RTS
                ser = open_scale_serial(device, baudrate=baudrate, timeout=1)
                status.available = True
            except (OSError, serial.SerialException) as exc:
                status.error = str(exc)
            finally:
                if ser:
                    ser.close()

        statuses.append(status)

    return statuses


def scale_connect():
    """Auto-detect a scale, connect, read weight, and return a ScaleReadResult.

    Scans all serial ports for candidate devices, connects to the first available one,
    reads weight using robust helpers, and returns a ScaleReadResult dataclass.

    Raises ScaleReadError if no port is found or no data is returned.
    """
    scale_connected = False
    ser = None
    last_error = None
    baudrate = getattr(settings, 'SCALE_BAUDRATE', 9600)
    timeout = getattr(settings, 'SCALE_READ_TIMEOUT', 2)

    for comport in serial.tools.list_ports.comports():
        device = comport.device

        if is_candidate_serial_port(device):
            try:
                # Use robust opener
                ser = open_scale_serial(device, baudrate=baudrate, timeout=timeout)
                scale_connected = True
                break
            except (OSError, serial.SerialException) as exc:
                last_error = str(exc)

    if not scale_connected or ser is None:
        raise ScaleReadError(last_error or 'Failed to connect to Serial Port Connection')

    try:
        # Use robust reader
        line = read_weight_from_serial(ser)

        if not line:
            raise ScaleReadError(
                'No data was returned from the connected scale. '
                'Check the cable, scale output mode, or port access.'
            )

        # Use robust parser
        weight, raw_str = parse_weight_from_bytes(line)

        if weight is None:
            raise ScaleReadError(f'Unable to parse a mass value from scale payload: {raw_str!r}')

        return ScaleReadResult(mass=weight, raw_payload=raw_str, serial_port=ser.port)
    finally:
        if ser:
            ser.close()


def read_scale():
    """Convenience wrapper for scale_connect()."""
    return scale_connect()
