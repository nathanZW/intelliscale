"""
Satellite Service for IntelliScale.
Background thread that continuously polls active scales for weight
when Satellite mode is enabled. Caches the latest weight per scale
so external services can read it instantly via the API.
"""
import threading
import time
import serial
import re
import logging

logger = logging.getLogger(__name__)


class SatelliteService:
    """Background service that continuously polls scales and caches weights."""

    def __init__(self):
        self._cached_weights = {}  # {scale_pk: {'weight': float, 'unit': str, 'timestamp': float, 'scale_name': str, 'scale_id': str}}
        self._lock = threading.Lock()
        self._thread = None
        self._running = False
        self._poll_interval = 1.0  # seconds between poll cycles

    def start(self):
        """Start the background polling thread."""
        if self._running:
            logger.info("Satellite service is already running.")
            return

        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="SatelliteService")
        self._thread.start()
        logger.info("Satellite service started.")

    def stop(self):
        """Stop the background polling thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("Satellite service stopped.")

    @property
    def is_running(self):
        return self._running

    def get_cached_weight(self, scale_pk):
        """Get the latest cached weight for a scale by primary key.
        
        Returns:
            dict with 'weight', 'unit', 'timestamp', 'scale_name', 'scale_id' or None
        """
        with self._lock:
            return self._cached_weights.get(scale_pk)

    def get_cached_weight_by_scale_id(self, scale_id_str):
        """Get the latest cached weight for a scale by its scale_id field.
        
        Returns:
            dict with 'weight', 'unit', 'timestamp', 'scale_name', 'scale_id', 'pk' or None
        """
        with self._lock:
            for pk, data in self._cached_weights.items():
                if data.get('scale_id') == scale_id_str:
                    return {**data, 'pk': pk}
            return None

    def get_all_cached_weights(self):
        """Get all cached weights."""
        with self._lock:
            return dict(self._cached_weights)

    def _poll_loop(self):
        """Main polling loop that runs in the background thread."""
        logger.info("Satellite polling loop started.")
        
        while self._running:
            try:
                self._poll_all_scales()
            except Exception as e:
                logger.error(f"Error in satellite poll loop: {e}")
            
            time.sleep(self._poll_interval)
        
        logger.info("Satellite polling loop ended.")

    def _poll_all_scales(self):
        """Poll all active scales with a configured COM port."""
        # Import here to avoid circular imports and ensure Django is ready
        from scale.models import Scale

        active_scales = Scale.objects.filter(is_active=True).exclude(com_port__isnull=True).exclude(com_port='')

        for scale in active_scales:
            try:
                weight, unit = self._read_weight_from_scale(scale)
                if weight is not None:
                    with self._lock:
                        self._cached_weights[scale.pk] = {
                            'weight': weight,
                            'unit': unit or 'kg',
                            'timestamp': time.time(),
                            'scale_name': scale.name,
                            'scale_id': str(scale.scale_id) if scale.scale_id else None,
                        }
            except Exception as e:
                logger.debug(f"Failed to read weight from scale {scale.name} ({scale.com_port}): {e}")

    def _read_weight_from_scale(self, scale):
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


# Module-level singleton
_service = SatelliteService()


def get_service():
    """Get the singleton SatelliteService instance."""
    return _service


def start_if_enabled():
    """Check CompanySettings and start the satellite service if enabled."""
    try:
        from scale.models import CompanySettings
        settings = CompanySettings.objects.first()
        if settings and settings.satellite:
            logger.info("Satellite mode is enabled in CompanySettings. Starting satellite service.")
            _service.start()
        else:
            logger.info("Satellite mode is not enabled. Background polling will not start.")
    except Exception as e:
        logger.warning(f"Could not check satellite setting on startup: {e}")
