"""
Scale management views for IntelliScale.
Handles scale CRUD, connection, and weight reading.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from users.views import is_admin
from ..models import Scale, ScaleIdHistory
from ..forms import ScaleForm
import serial
import serial.tools.list_ports
import re
import time


@login_required
@user_passes_test(is_admin)
def scale_list(request):
    scales = Scale.objects.all().order_by('name')
    return render(request, 'scale/scale_list.html', {'scales': scales})


@login_required
@user_passes_test(is_admin)
def scale_detail(request, pk):
    scale = get_object_or_404(Scale, pk=pk)
    history = ScaleIdHistory.objects.filter(scale=scale)
    return render(request, 'scale/scale_detail.html', {'scale': scale, 'history': history})


@login_required
@user_passes_test(is_admin)
def scale_create(request):
    if request.method == 'POST':
        form = ScaleForm(request.POST)
        if form.is_valid():
            scale = form.save()
            messages.success(request, f'Scale {scale.name} was created successfully.')
            return redirect('scale:scale_list')
    else:
        form = ScaleForm()
    
    return render(request, 'scale/scale_create.html', {'form': form})


@login_required
@user_passes_test(is_admin)
def scale_edit(request, pk):
    scale = get_object_or_404(Scale, pk=pk)
    old_scale_id = scale.scale_id
    
    if request.method == 'POST':
        form = ScaleForm(request.POST, instance=scale)
        if form.is_valid():
            new_scale_id = form.cleaned_data['scale_id']
            if old_scale_id != new_scale_id:
                ScaleIdHistory.objects.create(
                    scale=scale,
                    changed_by=request.user,
                    old_id=old_scale_id,
                    new_id=new_scale_id
                )
            scale = form.save()
            messages.success(request, f'Scale {scale.name} was updated successfully.')
            return redirect('scale:scale_detail', pk=scale.pk)
    else:
        form = ScaleForm(instance=scale)
    
    return render(request, 'scale/scale_edit.html', {'form': form, 'scale': scale})


@login_required
@user_passes_test(is_admin)
def scale_delete(request, pk):
    scale = get_object_or_404(Scale, pk=pk)
    
    if request.method == 'POST':
        name = scale.name
        scale.delete()
        messages.success(request, f'Scale {name} was deleted successfully.')
        return redirect('scale:scale_list')
    
    # If not POST, redirect to detail page
    return redirect('scale:scale_detail', pk=pk)


@login_required
def connect_scale_view(request, scale_id):
    if request.method == 'POST':
        try:
            scale = get_object_or_404(Scale, pk=scale_id)
            success, message = connect_scale(scale)
            
            if success:
                # Update scale status
                scale.last_connection_status = "connected"
                scale.last_seen = timezone.now()
                scale.save()
                
                return JsonResponse({
                    'success': True,
                    'message': message,
                    'status': scale.last_connection_status,
                    'last_seen': scale.last_seen.strftime('%Y-%m-%d %H:%M:%S')
                })
            else:
                return JsonResponse({
                    'success': False,
                    'message': message
                })
                
        except Exception as e:
            return JsonResponse({
                'success': False,
                'message': str(e)
            })
    
    return JsonResponse({
        'success': False,
        'message': 'Only POST requests are allowed.'
    })


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


def connect_scale(scale):
    """
    Tests that the scale's serial port can be opened.
    Does NOT attempt to read data — that's deferred to get_weight().
    The old scale_server_ex.py also only opened the port at connect time.
    """
    ser = None
    baud_rate = scale.baud_rate or 9600
    parity = scale.parity or 'N'
    stopbits = scale.stop_bits or 1
    bytesize = scale.data_bits or 8
    
    ports_to_try = []
    if scale.com_port:
        ports_to_try.append(scale.com_port)

    # Attempt to connect to the specified port first
    for port in ports_to_try:
        print(f"Attempting to connect to specified port: {port} for scale {scale.name}")
        
        # Try 1: With configured serial parameters
        try:
            ser = serial.Serial(
                port=port,
                baudrate=baud_rate,
                timeout=1,
                parity=parity,
                stopbits=stopbits,
                bytesize=bytesize,
                exclusive=True
            )
            if ser.is_open:
                print(f"Successfully opened port {port} for scale {scale.name}.")
                ser.close()
                return True, f"Successfully connected to {scale.name} on {port}."
        except serial.SerialException as e:
            print(f"SerialException on port {port} for scale {scale.name}: {str(e)}")
            if ser and ser.is_open:
                ser.close()
        except Exception as e:
            print(f"General Exception on port {port} for scale {scale.name}: {str(e)}")
            if ser and ser.is_open:
                ser.close()
        
        # Try 2: With pyserial defaults only (like scale_server_ex.py did)
        # Some scales don't work with explicit baud/parity/stopbits settings
        print(f"Retrying {port} with pyserial defaults (no explicit serial params)...")
        try:
            ser = serial.Serial(port, timeout=1, exclusive=True)
            if ser.is_open:
                print(f"Successfully opened port {port} with defaults for scale {scale.name}.")
                ser.close()
                return True, f"Successfully connected to {scale.name} on {port} (using defaults)."
        except serial.SerialException as e:
            print(f"SerialException on port {port} (defaults) for scale {scale.name}: {str(e)}")
            if ser and ser.is_open:
                ser.close()
        except Exception as e:
            print(f"General Exception on port {port} (defaults) for scale {scale.name}: {str(e)}")
            if ser and ser.is_open:
                ser.close()

    # If specified port failed or was not provided, attempt auto-detection
    print(f"Specified port connection failed or port not set for {scale.name}. Attempting auto-detection.")
    available_comports = serial.tools.list_ports.comports()
    print(f"Available COM ports for auto-detection: {[p.device for p in available_comports]}")

    for comport_info in available_comports:
        port_device = comport_info.device
        # Skip if this is the same as a specified port that already failed
        if scale.com_port and port_device == scale.com_port:
            continue

        # Check for common port name patterns
        if 'ttyUSB' in port_device.upper() or 'COM' in port_device.upper() or 'SERIAL' in port_device.upper():
            print(f"Auto-detect: Trying port {port_device} for scale {scale.name}")
            
            # Try with configured params
            try:
                ser = serial.Serial(
                    port=port_device,
                    baudrate=baud_rate,
                    timeout=1,
                    parity=parity,
                    stopbits=stopbits,
                    bytesize=bytesize,
                    exclusive=True
                )
                if ser.is_open:
                    print(f"Auto-detect: Successfully opened port {port_device} for scale {scale.name}.")
                    ser.close()
                    scale.com_port = port_device
                    return True, f"Successfully connected to {scale.name} on {port_device} (auto-detected)."
            except serial.SerialException as e:
                print(f"Auto-detect: SerialException on {port_device} for {scale.name}: {str(e)}")
                if ser and ser.is_open:
                    ser.close()
            except Exception as e:
                print(f"Auto-detect: General Exception on {port_device} for {scale.name}: {str(e)}")
                if ser and ser.is_open:
                    ser.close()
            
            # Try with pyserial defaults
            print(f"Auto-detect: Retrying {port_device} with pyserial defaults...")
            try:
                ser = serial.Serial(port_device, timeout=1, exclusive=True)
                if ser.is_open:
                    print(f"Auto-detect: Opened {port_device} with defaults for scale {scale.name}.")
                    ser.close()
                    scale.com_port = port_device
                    return True, f"Successfully connected to {scale.name} on {port_device} (auto-detected, defaults)."
            except serial.SerialException as e:
                print(f"Auto-detect: SerialException on {port_device} (defaults): {str(e)}")
                if ser and ser.is_open:
                    ser.close()
            except Exception as e:
                print(f"Auto-detect: General Exception on {port_device} (defaults): {str(e)}")
                if ser and ser.is_open:
                    ser.close()
                    
    return False, f"Could not connect to scale {scale.name}. No suitable COM port found or scale not responsive."


@login_required
def get_weight(request, scale_id):
    if request.method == 'POST':
        try:
            scale = get_object_or_404(Scale, pk=scale_id)
            
            # If scale is not connected, try to connect it first
            if scale.last_connection_status != "connected":
                success, msg = connect_scale(scale)
                if success:
                    scale.last_connection_status = "connected"
                    scale.last_seen = timezone.now()
                    scale.save()
                else:
                    return JsonResponse({
                        'success': False,
                        'message': f'Scale not connected: {msg}'
                    })
            
            # Try to read from the scale
            ser = None
            try:
                # Try with configured params first, fall back to defaults
                try:
                    ser = serial.Serial(
                        port=scale.com_port,
                        baudrate=scale.baud_rate or 9600,
                        timeout=scale.timeout or 1,
                        parity=scale.parity or 'N',
                        stopbits=scale.stop_bits or 1,
                        bytesize=scale.data_bits or 8
                    )
                except serial.SerialException:
                    # Fallback: open with just port and timeout (like scale_server_ex.py)
                    ser = serial.Serial(scale.com_port, timeout=scale.timeout or 1)
                
                if ser.is_open:
                    #clear any stale data from buffer
                    for _ in range(3):
                        ser.reset_input_buffer()
                        time.sleep(0.05)
                    
                    # Read response using protocol-aware helper
                    line = read_weight_from_serial(ser)
                    
                    if not line:
                        print(f"Scale {scale.name} connected but returned no data.")
                        return JsonResponse({
                            'success': False,
                            'message': 'Scale connected but returned no data. Check connection and scale settings.'
                        })

                    # Parse weight using format-aware parser
                    weight, raw_str = parse_weight_from_bytes(line)
                    
                    # Concise log: show weight + truncated raw bytes
                    raw_preview = repr(line[:60]) + ('...' if len(line) > 60 else '')
                    print(f"Scale {scale.name}: weight={weight} raw={raw_preview}")

                    if weight is not None:
                        return JsonResponse({
                            'success': True,
                            'weight': weight
                        })
                    else:
                        print(f"No numeric weight found in '{raw_str}' for scale {scale.name}")
                        return JsonResponse({
                            'success': False,
                            'message': f'No numeric weight found in: {raw_str}'
                        })
                        
            except serial.SerialException as e:
                print(f"SerialException for scale {scale.name}: {str(e)}")
                return JsonResponse({
                    'success': False,
                    'message': f'Error reading from scale: {str(e)}'
                })
            finally:
                if ser and ser.is_open:
                    ser.close()
                    
        except Exception as e:
            print(f"General exception in get_weight for scale {scale_id}: {str(e)}")
            return JsonResponse({
                'success': False,
                'message': str(e)
            })
    
    return JsonResponse({
        'success': False,
        'message': 'Only POST requests are allowed.'
    })


@csrf_exempt
def get_current_weight_api(request, scale_id):
    try:
        try:
            scale = Scale.objects.get(scale_id=str(scale_id))
        except Scale.DoesNotExist:
            return JsonResponse({
                'success': False,
                'message': f'Scale not found with scale_id: {scale_id}'
            }, status=404)
        
        if not scale.com_port:
            return JsonResponse({
                'success': False,
                'message': f'Scale {scale.name} does not have a COM port configured'
            }, status=400)
        
        ser = None
        try:
            try:
                ser = serial.Serial(
                    port=scale.com_port,
                    baudrate=scale.baud_rate or 9600,
                    timeout=scale.timeout or 1,
                    parity=scale.parity or 'N',
                    stopbits=scale.stop_bits or 1,
                    bytesize=scale.data_bits or 8
                )
            except serial.SerialException:
                # Fallback: open with just port and timeout (like scale_server_ex.py)
                ser = serial.Serial(scale.com_port, timeout=scale.timeout or 1)
            
            if ser.is_open:
                # Clear any stale data from buffer
                for _ in range(3):
                    ser.reset_input_buffer()
                    time.sleep(0.05)
                
                # Read response using protocol-aware helper
                line = read_weight_from_serial(ser)
                
                if not line:
                     return JsonResponse({
                        'success': False,
                        'message': 'Scale connected but returned no data.'
                    })

                # Parse weight using format-aware parser
                weight, raw_str = parse_weight_from_bytes(line)
                
                if weight is not None:
                    return JsonResponse({
                        'success': True,
                        'weight': weight,
                        'scale_id': str(scale.scale_id) if scale.scale_id else None,
                        'scale_name': scale.name
                    })
                else:
                    return JsonResponse({
                        'success': False,
                        'message': f'No numeric weight found in: {raw_str}'
                    })
                    
        except serial.SerialException as e:
            return JsonResponse({
                'success': False,
                'message': f'Error reading from scale: {str(e)}'
            })
        finally:
            if ser and ser.is_open:
                ser.close()
                
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': str(e)
        })
