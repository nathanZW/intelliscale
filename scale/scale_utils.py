"""
Shared utility functions for scale serial communication.
Used by both the satellite service and the Django views.
"""
import os
import re
import time
import serial
import random
import logging

logger = logging.getLogger(__name__)


def is_candidate_serial_port(device):
    """
    Return True only for device names that actually look like serial adapters.
    This avoids false positives like "Bluetooth-Incoming-Port", which happens
    to contain the substring "com" in "incoming".
    """
    if not device:
        return False

    normalized = device.strip().lower()
    basename = os.path.basename(normalized)

    if re.fullmatch(r'com\d+', basename):
        return True

    if normalized.startswith('/dev/serial/by-id/') or normalized.startswith('/dev/serial/by-path/'):
        return True

    candidate_tokens = (
        'ttyusb',
        'ttyacm',
        'ttyama',
        'usbserial',
        'usbmodem',
        'wchusbserial',
        'slab_usbto',
        'serial',
    )
    return any(token in basename for token in candidate_tokens)


def run_with_retry(func, max_retries=3, delay=0.1):
    """
    Runs a serial-related function with retries for transient SerialExceptions.
    Specifically handles the case where the port is ready but returns no data,
    which often indicates another process grabbed the data.
    """
    last_exception = None
    for attempt in range(max_retries):
        try:
            return func()
        except serial.SerialException as e:
            last_exception = e
            error_msg = str(e).lower()
            # Check for various contention-related error messages
            if "readiness to read but returned no data" in error_msg or \
               "multiple access on port" in error_msg or \
               "device disconnected" in error_msg or \
               "could not exclusively lock port" in error_msg or \
               "resource temporarily unavailable" in error_msg or \
               "device or resource busy" in error_msg:
                
                # Jittered delay to allow other processes to finish
                wait_time = delay * (2 ** attempt) + random.uniform(0, 0.05)
                logger.warning(f"Serial port contention detected (attempt {attempt+1}/{max_retries}). Retrying in {wait_time:.3f}s...")
                time.sleep(wait_time)
                continue
            else:
                # Other serial exceptions might be fatal (e.g. permission denied, no such port)
                raise
        except Exception:
            # Re-raise non-serial exceptions immediately
            raise
    
    # If we exhausted retries, raise the last encountered serial exception
    if last_exception:
        raise last_exception


def open_scale_serial(port, baudrate=9600, timeout=1, exclusive=True, **kwargs):
    """
    Safely opens a serial port while explicitly disabling DTR and RTS toggling.
    This prevents USB-serial adapters (commonly used with CAS CI-200A or Mettler scales)
    from performing a hardware reset upon connection, which causes Linux drivers to
    momentarily return EOF (0 bytes) yielding a "readiness but returned no data" error.
    """
    ser = serial.Serial()
    ser.port = port
    ser.baudrate = baudrate
    ser.timeout = timeout
    ser.exclusive = exclusive
    
    # Apply any other kwargs like parity, stopbits, bytesize
    for k, v in kwargs.items():
        setattr(ser, k, v)
        
    # Prevent hardware resets on open by explicitly turning off DTR and RTS
    ser.dtr = False
    ser.rts = False
    
    ser.open()
    
    # Tiny stabilization delay so the USB driver settles before the first read/tcflush
    time.sleep(0.05)
    
    return ser


def _parse_legacy_fixed_width_mass(raw_text):
    """
    Parse older scale-head payloads that place the mass in fixed columns.
    Keep this narrow so chopped fragments do not become real weights.
    """
    if not raw_text:
        return None

    cleaned = raw_text.replace('=', ' ')

    for start, stop in ((7, 14), (5, 8)):
        if len(cleaned) < stop:
            continue

        fragment = cleaned[start:stop].strip().replace(',', '.')
        if not re.fullmatch(r'-?\d+(?:\.\d+)?', fragment):
            continue

        try:
            weight = float(fragment)
        except ValueError:
            continue

        if weight > 5000:
            continue

        return weight

    return None


def _parse_repeated_delimited_mass(raw_text):
    """
    Parse heads that repeat the same bare numeric value separated by "=".
    Example: "= 0014.0= 0014.0= 0014.0"

    We keep this strict to avoid turning chopped fragments into weights:
    there must be at least two numeric tokens, they must all match, and the
    non-numeric separators may only be spaces or "=" characters.
    """
    if not raw_text:
        return None

    cleaned = raw_text.strip()
    if not cleaned or re.search(r'[a-zA-Z]', cleaned):
        return None

    numbers = re.findall(r'[-+]?\d+(?:\.\d+)?', cleaned.replace(',', '.'))
    if len(numbers) < 2:
        return None

    first = numbers[0].lstrip('+')
    if any(number.lstrip('+') != first for number in numbers[1:]):
        return None

    separators = re.sub(r'[-+]?\d+(?:\.\d+)?', '', cleaned.replace(',', '.'))
    if re.search(r'[^=\s]', separators):
        return None

    try:
        weight = float(first)
    except ValueError:
        return None

    if weight > 5000:
        return None

    return weight


def _looks_like_complete_unterminated_packet(raw_part):
    """
    Decide whether a packet without CR/LF still looks complete enough to parse.
    This supports fixed-width and unit-tagged scale heads without accepting
    short ghost fragments.
    """
    if not raw_part:
        return False

    if _parse_legacy_fixed_width_mass(raw_part) is not None:
        return True

    if _parse_repeated_delimited_mass(raw_part) is not None:
        return True

    stripped = raw_part.strip()
    if not stripped:
        return False

    if re.search(r'(kg|g|lbs|lb|pd)\b', stripped, re.IGNORECASE):
        upper = stripped.upper()
        if upper.endswith('KG G') or upper.endswith('KG N'):
            return len(raw_part) >= 12
        return len(raw_part) >= 8

    return False


def parse_weight_from_bytes(line):
    """
    Parses weight from raw scale bytes. Handles multiple formats:
    
    1. STX-framed format: 0x02 STATUS   WEIGHT   HUNDREDTHS 0x0D
       e.g. b'\\x0230     02    00\\r' -> 0.2 kg (display shows 0.2)
       The scale does NOT transmit the decimal point. The raw integer in the
       WEIGHT field is the display value × 10. We divide by 10 to recover it.
       HUNDREDTHS field adds sub-digit precision (usually 00).
       Multiple readings may be concatenated; takes the last one.
    
    2. Unit-suffixed format: "+ 1.23 kg" or "100.5 lbs"
    
    3. Simple numeric: strips non-numeric chars and parses.
    
    Returns (weight_float, raw_string) on success, (None, raw_string) on failure.
    """
    if not line:
        return None, ''
    
    # --- Format 1: STX-framed (0x02 ... 0x0D) ---
    if b'\x02' in line:
        # Split by STX to handle multiple concatenated readings
        readings = line.split(b'\x02')
        # Take the last non-empty reading
        for reading in reversed(readings):
            reading = reading.rstrip(b'\r\n')
            if not reading:
                continue
            decoded_reading = reading.decode('utf-8', errors='ignore').strip()
            parts = decoded_reading.split()
            if len(parts) >= 3:
                # Format: STATUS WEIGHT HUNDREDTHS
                # e.g. ['30', '02', '00'] -> 02.00 / 10 = 0.2 kg
                try:
                    raw_value = float(f"{parts[1]}.{parts[2]}")
                    weight = raw_value / 10.0
                    return weight, decoded_reading
                except (ValueError, IndexError):
                    pass
            elif len(parts) == 2:
                # Might be just WEIGHT HUNDREDTHS without status
                try:
                    raw_value = float(f"{parts[0]}.{parts[1]}")
                    weight = raw_value / 10.0
                    return weight, decoded_reading
                except (ValueError, IndexError):
                    pass
    
    # --- Decode for remaining parsers ---
    try:
        decoded = line.decode('utf-8', errors='ignore')
    except Exception:
        decoded = line.decode(errors='ignore')
        
    # Split by common terminators to isolate individual readings
    # We replace \r with \n, then split by \n
    parts = decoded.replace('\r', '\n').split('\n')
    
    # If the raw bytes didn't end with a terminator, the last chunk in `parts`
    # is likely an incomplete partial payload cut off mid-transmission.
    if not (line.endswith(b'\n') or line.endswith(b'\r')):
        if len(parts) > 1:
            parts.pop()
        else:
            # Some scale heads emit fixed-width packets without CR/LF. Only keep
            # those when the payload looks complete enough to trust.
            if b'\x02' not in line and not _looks_like_complete_unterminated_packet(parts[0]):
                return None, decoded.strip()
        
    # Iterate forwards to grab the first valid parse we can find
    for part in parts:
        raw_part = part
        # Before stripping, a valid scale packet almost always has leading padding spaces
        # (e.g. "   39.5 KG G"). A fragment chopped by USB buffer dropping (like "5 KG G") 
        # usually lacks them unless the buffer chopped right inside the padding.
        # We will use the unstripped length and structure as a strong hint.
        original_len = len(part)
        part = part.strip()
        if not part:
            continue

        # --- Format 2: Repeated bare numeric packets (e.g. "= 0014.0= 0014.0") ---
        repeated_weight = _parse_repeated_delimited_mass(raw_part)
        if repeated_weight is not None:
            return repeated_weight, part
            
        # --- Format 3: Unit-suffixed (e.g. "+ 1.23 kg" or "  39.5 KG G") ---
        # A valid packet from this scale is typically ~11+ characters long.
        # A fragment like "5 KG G" is only 6 characters.
        # We use a strict match. It must have boundaries so it doesn't just
        # rip a number out of the middle of a garbled string.
        # This regex looks for: Optional sign -> Number -> spaces -> Unit -> Word Boundary
        numeric_match = re.search(r'([-+]?\s*\d+(?:[.,]\d+)?)\s*(kg|g|lbs|lb|pd)\b', part, re.IGNORECASE)
        if numeric_match:
            # Reject if there are plausible numbers/letters before the match. This ensures
            # we don't extract "5.0" out of a fragmented "3.5.0", while allowing \x00-\x1f noise.
            prefix = part[:numeric_match.start()]
            
            # Allow common scale prefixes like ST,GS, or US,NT,
            prefix_stripped = re.sub(r'^[a-zA-Z]{2}[, ][a-zA-Z]{2}[, ]', '', prefix).strip()
            
            if re.search(r'[a-zA-Z0-9.,]', prefix_stripped):
                continue
                
            # Protect against mid-packet UART byte drops for scales that output space-padded "KG G" / "KG N" formats.
            # A valid string like "   39.5 KG G" is exactly 12 characters before stripping.
            # A dropped packet mid-transmission like "  1.0 KG G" is 10 chars.
            if part.upper().endswith('KG G') or part.upper().endswith('KG N'):
                if original_len < 12:
                    continue
            else:
                # Generic scale payload protection against bizarre 3-character drops like "1kg"
                if original_len < 4:
                    continue
                 
            num_str = numeric_match.group(1).replace(',', '.').replace(' ', '')
            try:
                weight = float(num_str)
                if weight > 5000:
                    continue
                
                return weight, part
            except ValueError:
                pass
        
        # --- Format 4: Simple numeric fallback ---
        # Only parse if the part is PURELY numeric, preventing ".5 KG G" from becoming "5.0".
        numeric_only = re.search(r'([-+]?\d+(?:[.,]\d+)?)\s*$', part)
        if numeric_only:
            prefix = part[:numeric_only.start()]
            
            # Allow common scale prefixes like ST,GS, or US,NT,
            prefix_stripped = re.sub(r'^[a-zA-Z]{2}[, ][a-zA-Z]{2}[, ]', '', prefix).strip()
            
            if re.search(r'[a-zA-Z0-9.,]', prefix_stripped):
                continue
                
            try:
                weight = float(numeric_only.group(1).replace(',', '.'))
                if weight > 5000:
                    continue
                return weight, part
            except ValueError:
                pass

        # --- Format 5: Legacy fixed-width payloads ---
        legacy_weight = _parse_legacy_fixed_width_mass(raw_part)
        if legacy_weight is not None:
            return legacy_weight, part
    
    return None, decoded.strip()


def read_weight_from_serial(ser):
    """
    Robustly reads weight data from the serial port.
    
    Since scales can be streaming continuously or waiting for a prompt,
    we try reading a line first. If that fails (timeout), we send a prompt
    and try again.
    """
    original_timeout = ser.timeout
    # Use a 1 second timeout. If a scale is streaming it will hit a newline
    # immediately. If it's prompted, it will reply within 1s.
    ser.timeout = 1
    
    try:
        # Clear any stale data that might be sitting in the buffer
        # (e.g. from a previous partial read)
        ser.reset_input_buffer()
        
        # Guard against the Linux tcflush driver bug where the next read returns 0 instantly
        time.sleep(0.02)

        # 1. Some scale heads emit fixed-width payloads without a line terminator.
        # A short raw read catches those before readline() discards the bytes.
        raw_chunk = ser.read(32)
        if raw_chunk:
            parsed_weight, _ = parse_weight_from_bytes(raw_chunk)
            if parsed_weight is not None or b'\n' in raw_chunk or b'\r' in raw_chunk:
                return raw_chunk
        
        # 2. Try reading a clean line (Continuous Output Mode or generic streaming)
        line = ser.readline()
        if line and (b'\n' in line or b'\r' in line):
            return line
            
        # 3. If nothing streamed, try MT-SICS "Send Immediate" command
        ser.write(b"SI\r\n")
        line = ser.readline()
        if line:
            return line
 
        # 4. Fallback to standard CR/LF trigger
        ser.write(b"\r\n")
        line = ser.readline()
        if line:
            return line
            
        # 5. Last resort: just read whatever is there (STX-framed formats sometimes lack \n)
        if ser.in_waiting > 0:
            return ser.read(ser.in_waiting)

        return b''
    finally:
        ser.timeout = original_timeout

