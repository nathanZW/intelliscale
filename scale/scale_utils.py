"""
Shared utility functions for scale serial communication.
Used by both the satellite service and the Django views.
"""
import re
import time


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
        decoded = line.decode('utf-8', errors='ignore').strip()
    except Exception:
        decoded = line.decode(errors='ignore').strip()
    
    # --- Format 2: Unit-suffixed (e.g. "+ 1.23 kg") ---
    numeric_match = re.search(r'([-+]?\s*\d+(?:[.,]\d+)?)\s*(kg|g|lbs|lb|pd)\b', decoded, re.IGNORECASE)
    if numeric_match:
        num_str = numeric_match.group(1).replace(',', '').replace(' ', '')
        try:
            weight = float(num_str)
            return weight, decoded
        except ValueError:
            pass
    
    # --- Format 3: Simple numeric fallback ---
    fallback_str = re.sub(r'[^0-9.,-]', '', decoded).strip('.,').strip()
    if fallback_str:
        try:
            weight = float(fallback_str.replace(',', ''))
            return weight, decoded
        except ValueError:
            pass
    
    return None, decoded


def read_weight_from_serial(ser):
    """
    Robustly reads weight data from the serial port, accommodating:
    1. Raw byte read (fastest — works for scales like the old scale_server_ex.py)
    2. Continuous Mode (streaming data with newline terminators)
    3. MT-SICS Command Mode (sending SI\\r\\n)
    4. Standard CR/LF polling
    
    Uses a short read timeout (1s) regardless of the port's configured timeout
    to avoid hanging the server.
    """
    # Temporarily set a short timeout for reads
    original_timeout = ser.timeout
    ser.timeout = 1
    
    try:
        # 1. Check if data is already waiting in the buffer (instant, no blocking)
        if ser.in_waiting > 0:
            line = ser.read(ser.in_waiting)
            if line:
                return line
        
        # 2. Try raw byte read — returns as soon as ANY bytes arrive (or timeout)
        #    This matches how scale_server_ex.py read from the scale
        line = ser.read(10)
        if line:
            return line

        # 3. Try MT-SICS "Send Immediate" command (some scales need a prompt)
        ser.write(b"SI\r\n")
        time.sleep(0.3)
        if ser.in_waiting > 0:
            line = ser.read(ser.in_waiting)
            if line:
                return line

        # 4. Fallback to standard CR/LF trigger
        ser.write(b"\r\n")
        time.sleep(0.3)
        if ser.in_waiting > 0:
            line = ser.read(ser.in_waiting)
            if line:
                return line

        return b''
    finally:
        # Restore original timeout
        ser.timeout = original_timeout
