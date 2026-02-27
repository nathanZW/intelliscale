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
import re
import logging

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
    
    Returns:
        (weight: float, unit: str) or (None, None) on failure
    """
    ser = None
    try:
        ser = serial.Serial(scale.com_port, 9600, timeout=2)
        if ser.is_open:
            # Clear stale data
            for _ in range(3):
                ser.reset_input_buffer()
                time.sleep(0.05)
            
            # Send command to trigger reading
            ser.write(b"\r\n")
            time.sleep(0.3)
            line = ser.readline()
            
            try:
                decoded = line.decode('utf-8', errors='ignore').strip()
            except Exception:
                decoded = line.decode(errors='ignore').strip()

            # Parse numeric weight
            numeric_match = re.search(
                r'([-+]?\d+(?:[.,]\d+)?)\s*(kg|g|lbs|lb|pd)\b',
                decoded,
                re.IGNORECASE
            )

            if numeric_match:
                num_str = numeric_match.group(1).replace(',', '')
                unit = numeric_match.group(2).lower()
                weight = float(num_str)
                return weight, unit

        return None, None

    except serial.SerialException:
        return None, None
    finally:
        if ser and ser.is_open:
            ser.close()


def poll_all_scales():
    """Poll all active scales and update the cache file."""
    from scale.models import Scale

    active_scales = Scale.objects.filter(is_active=True).exclude(
        com_port__isnull=True
    ).exclude(com_port='')

    cached = read_cached_weights()

    for scale in active_scales:
        try:
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
