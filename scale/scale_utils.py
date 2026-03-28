"""
Shared utility functions for scale serial communication.
Used by both the satellite service and the Django views.
"""
import re
import time
import serial


TRANSIENT_EMPTY_READ_ERROR = 'device reports readiness to read but returned no data'


def open_serial_for_scale(scale, port=None, timeout=None, exclusive=None):
    """
    Open a serial connection using the scale's configured parameters first,
    then fall back to pyserial defaults if that fails.
    """
    serial_timeout = timeout if timeout is not None else (scale.timeout or 1)
    port = port or scale.com_port

    configured_kwargs = {
        'port': port,
        'baudrate': scale.baud_rate or 9600,
        'timeout': serial_timeout,
        'parity': scale.parity or 'N',
        'stopbits': scale.stop_bits or 1,
        'bytesize': scale.data_bits or 8,
    }
    if exclusive is not None:
        configured_kwargs['exclusive'] = exclusive

    try:
        return serial.Serial(**configured_kwargs)
    except (serial.SerialException, ValueError):
        fallback_kwargs = {
            'port': port,
            'timeout': serial_timeout,
        }
        if exclusive is not None:
            fallback_kwargs['exclusive'] = exclusive
        return serial.Serial(**fallback_kwargs)


def _is_transient_empty_read_error(exc):
    return TRANSIENT_EMPTY_READ_ERROR in str(exc)


def _safe_in_waiting(ser):
    try:
        return ser.in_waiting
    except serial.SerialException as exc:
        if _is_transient_empty_read_error(exc):
            return 0
        raise


def _safe_read(ser, size):
    try:
        return ser.read(size)
    except serial.SerialException as exc:
        if _is_transient_empty_read_error(exc):
            return b''
        raise


def _read_until_idle(ser, wait_for_first_byte, settle_time=0.05, max_wait=1.0):
    """
    Wait until the serial buffer stops growing, then read the accumulated bytes.
    """
    deadline = time.monotonic() + max_wait
    current_count = _safe_in_waiting(ser)

    if wait_for_first_byte:
        while current_count == 0:
            if time.monotonic() >= deadline:
                return b''
            time.sleep(0.01)
            current_count = _safe_in_waiting(ser)
    elif current_count == 0:
        return b''

    prev_count = current_count
    last_change = time.monotonic()

    while True:
        time.sleep(0.02)
        now = time.monotonic()
        current_count = _safe_in_waiting(ser)

        if current_count != prev_count:
            prev_count = current_count
            last_change = now
        elif (now - last_change) >= settle_time or now >= deadline:
            break

    final_count = _safe_in_waiting(ser)
    if final_count <= 0:
        return b''

    return _safe_read(ser, final_count)


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

    # --- Format 0: CAS fixed-width payload with no terminator ---
    # Observed live output from a CI-200A-C4 over USB:
    #   b'= 0004.0'
    # This format is a complete packet even though it does not end in CR/LF.
    try:
        decoded = line.decode('utf-8', errors='ignore')
    except Exception:
        decoded = line.decode(errors='ignore')

    cas_fixed_width_match = re.fullmatch(r'[=+-]?\s*\d+(?:[.,]\d+)?\s*', decoded)
    if cas_fixed_width_match and 6 <= len(line) <= 10:
        numeric_text = re.sub(r'^[= ]+', '', decoded).strip()
        try:
            return float(numeric_text.replace(',', '.')), decoded.strip()
        except ValueError:
            pass
    
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
    # Split by common terminators to isolate individual readings
    # We replace \r with \n, then split by \n
    parts = decoded.replace('\r', '\n').split('\n')
    
    # If the raw bytes didn't end with a terminator, the last chunk in `parts`
    # is likely an incomplete partial payload cut off mid-transmission.
    if not (line.endswith(b'\n') or line.endswith(b'\r')):
        if len(parts) > 1:
            parts.pop()
        else:
            # It's a single fragment and it doesn't end in newline. 
            # Unless it's STX-framed, this is almost certainly an incomplete packet.
            if b'\x02' not in line:
                return None, decoded.strip()
        
    # Iterate forwards to grab the first valid parse we can find
    for part in parts:
        # Before stripping, a valid scale packet almost always has leading padding spaces
        # (e.g. "   39.5 KG G"). A fragment chopped by USB buffer dropping (like "5 KG G") 
        # usually lacks them unless the buffer chopped right inside the padding.
        # We will use the unstripped length and structure as a strong hint.
        original_len = len(part)
        part = part.strip()
        if not part:
            continue
            
        # --- Format 2: Unit-suffixed (e.g. "+ 1.23 kg" or "  39.5 KG G") ---
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
            
            # Allow common scale prefixes like ST,GS, or US,NT, or bare 1-2 letter codes like ww
            prefix_stripped = re.sub(r'^[a-zA-Z]{2}[, ][a-zA-Z]{2}[, ]', '', prefix)
            prefix_stripped = re.sub(r'^[a-zA-Z]{1,2}$', '', prefix_stripped).strip()
            
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
        
        # --- Format 3: Simple numeric fallback ---
        # Only parse if the part is PURELY numeric, preventing ".5 KG G" from becoming "5.0".
        numeric_only = re.search(r'([-+]?\d+(?:[.,]\d+)?)\s*$', part)
        if numeric_only:
            prefix = part[:numeric_only.start()]
            
            # Allow common scale prefixes like ST,GS, or US,NT, or bare 1-2 letter codes like ww
            prefix_stripped = re.sub(r'^[a-zA-Z]{2}[, ][a-zA-Z]{2}[, ]', '', prefix)
            prefix_stripped = re.sub(r'^[a-zA-Z]{1,2}$', '', prefix_stripped).strip()
            
            if re.search(r'[a-zA-Z0-9.,]', prefix_stripped):
                continue
                
            try:
                weight = float(numeric_only.group(1).replace(',', '.'))
                if weight > 5000:
                    continue
                return weight, part
            except ValueError:
                pass
    
    return None, decoded.strip()


def read_weight_from_serial(ser):
    """
    Robustly reads weight data from the serial port.

    Supports continuously streaming scales, prompt-driven scales, and
    CAS CI-200 request/command modes.
    """
    original_timeout = ser.timeout
    ser.timeout = 1

    try:
        # If the device is already streaming, keep the buffered packet instead of
        # discarding it. Some indicators begin transmitting immediately on open.
        line = _read_until_idle(ser, wait_for_first_byte=False, settle_time=0.05, max_wait=0.2)
        if line:
            return line

        prompt_attempts = (
            b"SI\r\n",      # MT-SICS immediate weight
            b"\r\n",        # generic CR/LF wakeup
            b"D00KW\r\n",   # CAS CI-200 command mode, default device id 00
            b"\x00WT\r\n",  # CAS/NT command mode, default device id 0x00
            b"\x00",        # CAS request mode with device id 00
        )

        for prompt in prompt_attempts:
            ser.write(prompt)
            line = _read_until_idle(ser, wait_for_first_byte=True, settle_time=0.05, max_wait=0.35)
            if line:
                return line

        return b''
    finally:
        ser.timeout = original_timeout
