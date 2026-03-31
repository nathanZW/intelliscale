"""
Shared utility functions for scale serial communication.
Used by both the satellite service and the Django views.
"""
import re
import time
import serial


TRANSIENT_EMPTY_READ_ERROR = 'device reports readiness to read but returned no data'
PROTOCOL_GENERIC = 'generic'
PROTOCOL_METTLER_TOLEDO = 'mettler_toledo'
PROTOCOL_CAS_STREAM = 'cas_stream'


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


def _safe_read(ser, size, attempts=2):
    for attempt in range(attempts + 1):
        try:
            return ser.read(size)
        except serial.SerialException as exc:
            if _is_transient_empty_read_error(exc) and attempt < attempts:
                time.sleep(0.02)
                continue
            raise
    return b''


def _safe_readline(ser, attempts=2):
    for attempt in range(attempts + 1):
        try:
            return ser.readline()
        except serial.SerialException as exc:
            if _is_transient_empty_read_error(exc) and attempt < attempts:
                time.sleep(0.02)
                continue
            raise
    return b''


def _safe_reset_input_buffer(ser):
    try:
        ser.reset_input_buffer()
    except serial.SerialException as exc:
        if not _is_transient_empty_read_error(exc):
            raise


def _read_until_idle(ser, wait_for_first_byte, max_wait=0.3, settle_time=0.05):
    deadline = time.monotonic() + max_wait
    last_data_at = None
    chunks = bytearray()

    while time.monotonic() < deadline:
        bytes_waiting = _safe_in_waiting(ser)

        if bytes_waiting > 0:
            chunk = _safe_read(ser, bytes_waiting)
            if chunk:
                chunks.extend(chunk)
                last_data_at = time.monotonic()
        elif chunks and last_data_at and (time.monotonic() - last_data_at) >= settle_time:
            break
        elif not chunks and not wait_for_first_byte:
            break

        time.sleep(0.02)

    return bytes(chunks)


def get_scale_protocol(scale):
    protocol = getattr(scale, 'protocol', None)
    if protocol:
        return protocol
    if getattr(scale, 'mettler_toledo', False):
        return PROTOCOL_METTLER_TOLEDO
    return PROTOCOL_GENERIC


def uses_mettler_protocol(scale):
    return get_scale_protocol(scale) == PROTOCOL_METTLER_TOLEDO


def uses_cas_stream_protocol(scale):
    return get_scale_protocol(scale) == PROTOCOL_CAS_STREAM


def read_cas_stream_sample(
    ser,
    max_wait=0.25,
    idle_window=0.05,
    read_size=32,
    max_buffer_bytes=32,
):
    """
    Read a short CAS stream sample without prompt writes or newline assumptions.
    """
    original_timeout = ser.timeout
    ser.timeout = max_wait
    deadline = time.monotonic() + max_wait
    last_data_at = None
    chunks = bytearray()

    try:
        while time.monotonic() < deadline:
            bytes_waiting = _safe_in_waiting(ser)

            if bytes_waiting > 0:
                chunk = _safe_read(ser, min(bytes_waiting, read_size))
            elif chunks:
                if last_data_at and (time.monotonic() - last_data_at) >= idle_window:
                    break
                time.sleep(0.01)
                continue
            else:
                chunk = _safe_read(ser, read_size)

            if chunk:
                chunks.extend(chunk)
                last_data_at = time.monotonic()
                if len(chunks) >= max_buffer_bytes:
                    break
                continue

            if not chunks:
                break

            time.sleep(0.01)

        return bytes(chunks)
    finally:
        ser.timeout = original_timeout


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

    try:
        decoded = line.decode('utf-8', errors='ignore')
    except Exception:
        decoded = line.decode(errors='ignore')

    # CAS CI-200A-C4 can emit fixed-width packets with no terminator, and they may
    # arrive as a short burst like b'= 0004.0= 0004.0'.
    cas_fixed_width_matches = re.findall(r'=\s*[+-]?\d+(?:[.,]\d+)?', decoded)
    if cas_fixed_width_matches:
        try:
            cas_packet = cas_fixed_width_matches[-1].strip()
            weight = float(cas_packet.replace('=', '', 1).strip().replace(',', '.'))
            if weight == 0:
                weight = 0.0
            return weight, cas_packet
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
    
    Since scales can be streaming continuously or waiting for a prompt,
    we try reading a line first. If that fails (timeout), we send a prompt
    and try again.
    """
    original_timeout = ser.timeout
    # Use a 1 second timeout. If a scale is streaming it will hit a newline
    # immediately. If it's prompted, it will reply within 1s.
    ser.timeout = 1
    
    try:
        buffered_line = _read_until_idle(ser, wait_for_first_byte=True, max_wait=0.3, settle_time=0.05)
        if buffered_line:
            weight, _ = parse_weight_from_bytes(buffered_line)
            if weight is not None:
                return buffered_line

        # Clear any stale data that might be sitting in the buffer
        # (e.g. from a previous partial read)
        _safe_reset_input_buffer(ser)
        
        # 1. Try reading a clean line (Continuous Output Mode or generic streaming)
        line = _safe_readline(ser)
        if line and (b'\n' in line or b'\r' in line):
            return line
            
        # 2. If nothing streamed, try MT-SICS "Send Immediate" command
        ser.write(b"SI\r\n")
        line = _safe_readline(ser)
        if line:
            return line

        # 3. Fallback to standard CR/LF trigger
        ser.write(b"\r\n")
        line = _safe_readline(ser)
        if line:
            return line
            
        # 4. Last resort: just read whatever is there (STX-framed formats sometimes lack \n)
        bytes_waiting = _safe_in_waiting(ser)
        if bytes_waiting > 0:
            return _safe_read(ser, bytes_waiting)

        return b''
    finally:
        ser.timeout = original_timeout


def read_weight_bytes_for_scale(scale, ser):
    if uses_cas_stream_protocol(scale):
        return read_cas_stream_sample(ser)
    return read_weight_from_serial(ser)
