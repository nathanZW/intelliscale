"""
Satellite Service for IntelliScale.
Runs as a standalone process (management command) that continuously polls 
active scales for weight and writes the results to a JSON cache file.
Gunicorn workers read from this cache file — no SQLite contention, which necessated the revert.
"""
import json
import os
import time
import serial
import serial.tools.list_ports
import logging
from scale.scale_utils import open_serial_for_scale, parse_weight_from_bytes, read_weight_from_serial

logger = logging.getLogger(__name__)

# Cache file location — sits alongside the Django project
CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.satellite_cache.json')


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


def _read_weight_from_scale(scale):
    """Read weight from a single scale via serial.
    
    Uses the scale's configured serial parameters (baud_rate, parity, etc.)
    and the shared read/parse helpers for robust, protocol-aware reading.
    
    Returns:
        (weight: float, unit: str) or (None, None) on failure
    """
    ser = None
    try:
        ser = open_serial_for_scale(scale, timeout=scale.timeout or 2)

        if ser.is_open:
            # Use shared multi-strategy reader (raw → MT-SICS → CR/LF)
            line = read_weight_from_serial(ser)
            
            if not line:
                return None, None

            # Use shared format-aware parser
            weight, raw_str = parse_weight_from_bytes(line)
            
            if weight is not None:
                # Extract unit if present in raw string
                import re
                unit_match = re.search(r'(kg|g|lbs|lb|pd)\b', raw_str, re.IGNORECASE)
                unit = unit_match.group(1).lower() if unit_match else 'kg'
                return weight, unit

        return None, None

    except serial.SerialException:
        return None, None
    finally:
        if ser and ser.is_open:
            ser.close()


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

    active_scales = Scale.objects.filter(is_active=True)

    cached = read_cached_weights()

    for scale in active_scales:
        try:
            # Auto-detect port if not set
            if not scale.com_port:
                if not _try_auto_detect_port(scale):
                    # No port found this cycle — skip silently
                    continue
            
            weight, unit = _read_weight_from_scale(scale)
            if weight is not None:
                scale_id_str = str(scale.scale_id) if scale.scale_id else str(scale.pk)
                cached[scale_id_str] = {
                    'weight': weight,
                    'unit': unit or 'kg',
                    'timestamp': time.time(),
                    'scale_name': scale.name,
                    'scale_id': str(scale.scale_id) if scale.scale_id else None,
                }
        except Exception as e:
            logger.debug(f"Failed to read weight from scale {scale.name}: {e}")

    _write_cache(cached)


def run_satellite_loop(poll_interval=1.0):
    """Main satellite loop. Runs indefinitely, polling scales.
    
    Args:
        poll_interval: Seconds between poll cycles (default 1.0)
    """
    logger.info(f"Satellite service started. Polling every {poll_interval}s. Cache file: {CACHE_FILE}")
    
    while True:
        try:
            poll_all_scales()
        except Exception as e:
            logger.error(f"Error in satellite poll loop: {e}")
        
        time.sleep(poll_interval)
