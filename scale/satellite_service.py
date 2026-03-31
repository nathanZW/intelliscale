"""
Satellite Service for IntelliScale.
Runs as a standalone process (management command) that continuously polls 
active scales for weight and writes the results to a JSON cache file.
Gunicorn workers read from this cache file — no SQLite contention, which necessated the revert.
"""
import json
import os
import re
import time
import serial
import serial.tools.list_ports
import logging
from scale.scale_utils import (
    get_scale_protocol,
    open_serial_for_scale,
    parse_weight_from_bytes,
    read_weight_bytes_for_scale,
)

logger = logging.getLogger(__name__)

# Cache file location — sits alongside the Django project
CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.satellite_cache.json')
PERSISTENT_SERIAL_READERS = {}
SERIAL_RECOVERY_DELAY_SECONDS = 0.5


def read_cached_weights():
    """Read cached weights from the JSON file.
    
    Returns:
        dict: {scale_id_str: {weight, unit, timestamp, scale_name, scale_id}} or empty dict
    """
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, 'r') as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError, OSError) as e:
        logger.debug(f"Could not read satellite cache: {e}")
    return {}


def get_cached_weight_by_scale_id(scale_id_str):
    """Get the latest cached weight for a scale by its scale_id field.
    
    Returns:
        dict with 'weight', 'unit', 'timestamp', 'scale_name', 'scale_id' or None
    """
    cached = read_cached_weights()
    return cached.get(str(scale_id_str))


def _write_cache(data):
    """Atomically write cached weights to the JSON file."""
    tmp_file = CACHE_FILE + '.tmp'
    try:
        with open(tmp_file, 'w') as f:
            json.dump(data, f)
        os.replace(tmp_file, CACHE_FILE)  # atomic on POSIX
    except (IOError, OSError) as e:
        logger.error(f"Could not write satellite cache: {e}")


def _cache_key_for_scale(scale):
    return str(scale.scale_id) if scale.scale_id else str(scale.pk)


def _touch_cache_entry(cached, scale):
    cache_key = _cache_key_for_scale(scale)
    entry = cached.get(cache_key, {})
    entry['scale_name'] = scale.name
    entry['scale_id'] = str(scale.scale_id) if scale.scale_id else None
    cached[cache_key] = entry
    return entry


def _mark_scale_cache_status(cached, scale, status, error=None):
    entry = _touch_cache_entry(cached, scale)
    entry['status'] = status
    entry['last_attempt'] = time.time()
    if error:
        entry['last_error'] = error
    else:
        entry.pop('last_error', None)


def _cache_successful_weight(cached, scale, weight, unit):
    entry = _touch_cache_entry(cached, scale)
    entry.update({
        'weight': weight,
        'unit': unit or 'kg',
        'timestamp': time.time(),
        'status': 'ok',
        'last_attempt': time.time(),
    })
    entry.pop('last_error', None)
    return entry


def _scale_reader_key(scale):
    return scale.pk


def _scale_reader_signature(scale):
    return (
        get_scale_protocol(scale),
        scale.com_port,
        scale.baud_rate or 9600,
        scale.timeout or 2,
        scale.parity or 'N',
        scale.stop_bits or 1,
        scale.data_bits or 8,
    )


def _close_persistent_reader(scale_key):
    state = PERSISTENT_SERIAL_READERS.pop(scale_key, None)
    if not state:
        return

    ser = state.get('ser')
    if ser and ser.is_open:
        try:
            ser.close()
        except Exception:
            pass


def _close_stale_readers(active_scale_keys):
    stale_keys = set(PERSISTENT_SERIAL_READERS) - set(active_scale_keys)
    for scale_key in stale_keys:
        _close_persistent_reader(scale_key)


def close_all_persistent_readers():
    for scale_key in list(PERSISTENT_SERIAL_READERS):
        _close_persistent_reader(scale_key)


def _port_is_available(port):
    return bool(port) and os.path.exists(port)


def _get_or_open_persistent_reader(scale):
    scale_key = _scale_reader_key(scale)
    signature = _scale_reader_signature(scale)
    state = PERSISTENT_SERIAL_READERS.get(scale_key)

    if state and state.get('signature') == signature:
        ser = state.get('ser')
        if ser and ser.is_open:
            return ser
        _close_persistent_reader(scale_key)
    elif state:
        _close_persistent_reader(scale_key)

    ser = open_serial_for_scale(scale, timeout=scale.timeout or 2)
    PERSISTENT_SERIAL_READERS[scale_key] = {
        'ser': ser,
        'signature': signature,
    }
    logger.info(f"Opened persistent serial reader for scale {scale.name} on {scale.com_port}")
    return ser


def _read_weight_from_scale(scale):
    """Read weight from a single scale via serial.
    
    Uses the scale's configured serial parameters (baud_rate, parity, etc.)
    and the shared read/parse helpers for robust, protocol-aware reading.
    
    Returns:
        (weight: float, unit: str, error: str|None)
    """
    scale_key = _scale_reader_key(scale)
    last_error = None

    for attempt in range(2):
        try:
            if not _port_is_available(scale.com_port):
                _close_persistent_reader(scale_key)
                if not _try_auto_detect_port(scale):
                    return None, None, f'Port unavailable: {scale.com_port}'

            ser = _get_or_open_persistent_reader(scale)

            if not ser.is_open:
                last_error = 'Serial port is not open'
                _close_persistent_reader(scale_key)
                continue

            line = read_weight_bytes_for_scale(scale, ser)
            if not line:
                last_error = 'Scale connected but returned no data'
                logger.warning(f"{scale.name}: {last_error} (attempt {attempt + 1})")
                _close_persistent_reader(scale_key)
            else:
                weight, raw_str = parse_weight_from_bytes(line)
                if weight is not None:
                    unit_match = re.search(r'(kg|g|lbs|lb|pd)\b', raw_str, re.IGNORECASE)
                    unit = unit_match.group(1).lower() if unit_match else 'kg'
                    return weight, unit, None

                last_error = f'No numeric weight found in: {raw_str}'
                logger.warning(f"{scale.name}: {last_error} (attempt {attempt + 1})")
                _close_persistent_reader(scale_key)

        except serial.SerialException as exc:
            last_error = str(exc)
            logger.warning(f"Persistent reader failed for scale {scale.name}: {exc}")
            _close_persistent_reader(scale_key)
        except Exception as exc:
            last_error = str(exc)
            logger.debug(f"Unexpected persistent reader error for scale {scale.name}: {exc}")
            _close_persistent_reader(scale_key)

        if attempt == 0:
            time.sleep(SERIAL_RECOVERY_DELAY_SECONDS)

    return None, None, last_error


def _try_auto_detect_port(scale):
    """Attempt to auto-detect a COM port for a scale.
    
    Scans available serial ports and tries to open each one using
    the scale's configured serial parameters.
    
    If a working port is found, updates scale.com_port and saves to DB.
    
    Returns:
        True if a port was detected and saved, False otherwise.
    """
    available_comports = serial.tools.list_ports.comports()
    
    for comport_info in available_comports:
        port_device = comport_info.device
        
        # Filter for common serial port patterns
        if not ('TTYUSB' in port_device.upper() or 'COM' in port_device.upper() or 'SERIAL' in port_device.upper()):
            continue
        
        ser = None
        # Try with the scale's configured serial parameters
        try:
            ser = serial.Serial(
                port=port_device,
                baudrate=scale.baud_rate or 9600,
                timeout=scale.timeout or 2,
                parity=scale.parity or 'N',
                stopbits=scale.stop_bits or 1,
                bytesize=scale.data_bits or 8
            )
            if ser.is_open:
                ser.close()
                scale.com_port = port_device
                scale.save(update_fields=['com_port'])
                logger.info(f"Auto-detected port {port_device} for scale {scale.name}. Saved to DB.")
                return True
        except serial.SerialException:
            if ser and ser.is_open:
                ser.close()
        except Exception:
            if ser and ser.is_open:
                ser.close()
        
        # Fallback: try with just port and timeout
        try:
            ser = serial.Serial(port_device, timeout=scale.timeout or 2)
            if ser.is_open:
                ser.close()
                scale.com_port = port_device
                scale.save(update_fields=['com_port'])
                logger.info(f"Auto-detected port {port_device} (defaults) for scale {scale.name}. Saved to DB.")
                return True
        except serial.SerialException:
            if ser and ser.is_open:
                ser.close()
        except Exception:
            if ser and ser.is_open:
                ser.close()
    
    return False


def poll_all_scales():
    """Poll all active scales and update the cache file.
    
    Scales with a saved com_port are read directly.
    Scales without a com_port trigger auto-detection first.
    """
    from scale.models import Scale

    active_scales = list(Scale.objects.filter(is_active=True))
    active_scale_keys = [scale.pk for scale in active_scales]

    cached = read_cached_weights()

    for scale in active_scales:
        try:
            # Auto-detect port if not set
            if not scale.com_port:
                if not _try_auto_detect_port(scale):
                    _close_persistent_reader(scale.pk)
                    _mark_scale_cache_status(cached, scale, 'no_port', 'No serial port detected')
                    # No port found this cycle — skip silently
                    continue
            
            weight, unit, error = _read_weight_from_scale(scale)
            if weight is not None:
                _cache_successful_weight(cached, scale, weight, unit)
            else:
                _mark_scale_cache_status(cached, scale, 'recovering', error or 'Unknown serial read failure')
        except Exception as e:
            _mark_scale_cache_status(cached, scale, 'error', str(e))
            logger.debug(f"Failed to read weight from scale {scale.name}: {e}")

    _close_stale_readers(active_scale_keys)
    _write_cache(cached)


def run_satellite_loop(poll_interval=1.0):
    """Main satellite loop. Runs indefinitely, polling scales.
    
    Args:
        poll_interval: Seconds between poll cycles (default 1.0)
    """
    logger.info(f"Satellite service started. Polling every {poll_interval}s. Cache file: {CACHE_FILE}")
    
    try:
        while True:
            try:
                poll_all_scales()
            except Exception as e:
                logger.error(f"Error in satellite poll loop: {e}")
            
            time.sleep(poll_interval)
    finally:
        close_all_persistent_readers()
