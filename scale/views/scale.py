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
from ..scale_utils import open_serial_for_scale, parse_weight_from_bytes, read_weight_from_serial


def _get_satellite_cached_weight(scale, tare_weight=0):
    from ..models import CompanySettings
    from ..satellite_service import get_cached_weight_by_scale_id

    settings = CompanySettings.objects.first()
    if not settings or not settings.satellite:
        return None

    cache_key = str(scale.scale_id) if scale.scale_id else str(scale.pk)
    cached = get_cached_weight_by_scale_id(cache_key)
    if not cached:
        return JsonResponse({
            'success': False,
            'message': (
                f'No cached weight available for {scale.name}. '
                'Ensure the satellite service is running (python manage.py run_satellite).'
            )
        })

    if 'weight' not in cached or 'timestamp' not in cached:
        return JsonResponse({
            'success': False,
            'message': cached.get('last_error') or f'{scale.name} has not produced a valid cached weight yet.',
            'source': 'satellite_cache',
            'status': cached.get('status', 'unknown'),
            'last_attempt': cached.get('last_attempt'),
            'last_error': cached.get('last_error'),
        })

    gross_weight = cached['weight']
    net_weight = gross_weight - tare_weight
    response = {
        'success': True,
        'weight': net_weight,
        'gross_weight': gross_weight,
        'unit': cached.get('unit', 'kg'),
        'scale_id': cached.get('scale_id'),
        'scale_name': cached.get('scale_name'),
        'source': 'satellite_cache',
        'cache_age_seconds': round(time.time() - cached['timestamp'], 2),
        'status': cached.get('status', 'ok'),
        'last_attempt': cached.get('last_attempt', cached['timestamp']),
        'last_error': cached.get('last_error'),
    }
    if tare_weight == 0:
        response['weight'] = gross_weight
    return JsonResponse(response)


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


def connect_scale(scale):
    if scale.mettler_toledo:
        return _connect_scale_mettler(scale)
    return _connect_scale_default(scale)


def _connect_scale_default(scale):
    ser = None
    timeout = scale.timeout or 2
    
    ports_to_try = []
    if scale.com_port:
        ports_to_try.append(scale.com_port)

    # Attempt to connect to the specified port first
    for port in ports_to_try:
        print(f"Attempting to connect to specified port: {port} for scale {scale.name}")
        try:
            ser = open_serial_for_scale(scale, port=port, timeout=timeout)
            if ser.is_open:
                print(f"Successfully opened port {port}. Trying to read data...")
                # Try a simple read to confirm responsiveness. Some scales send data on connect or CR.
                ser.write(b"\r\n") # Send a CR/LF, might elicit a response
                line = ser.readline()
                print(f"Read from {port}: {line.decode(errors='ignore').strip()}")
                ser.close()
                # If we successfully opened, (optionally read), and closed, consider it a success.
                # The `connect_scale_view` will update status and last_seen.
                # If the port was specified and worked, no need to change scale.com_port.
                return True, f"Successfully connected to {scale.name} on {port}."
        except serial.SerialException as e:
            print(f"SerialException on port {port} for scale {scale.name}: {str(e)}")
            if ser and ser.is_open:
                ser.close()
        except Exception as e:
            print(f"General Exception on port {port} for scale {scale.name}: {str(e)}")
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
        if 'TTYUSB' in port_device.upper() or 'COM' in port_device.upper() or 'SERIAL' in port_device.upper():
            print(f"Auto-detect: Trying port {port_device} for scale {scale.name}")
            try:
                ser = open_serial_for_scale(scale, port=port_device, timeout=timeout)
                if ser.is_open:
                    print(f"Successfully opened auto-detected port {port_device}. Trying to read...")
                    ser.write(b"\r\n")
                    line = ser.readline()
                    print(f"Read from auto-detected {port_device}: {line.decode(errors='ignore').strip()}")
                    ser.close()
                    # Update the scale's com_port with the auto-detected one
                    scale.com_port = port_device 
                    print(f"Auto-detected working port {port_device} for {scale.name}. Scale's com_port updated.")
                    return True, f"Successfully connected to {scale.name} on {port_device} (auto-detected)."
            except serial.SerialException as e:
                print(f"Auto-detect: SerialException on {port_device} for {scale.name}: {str(e)}")
                if ser and ser.is_open:
                    ser.close()
            except Exception as e:
                print(f"Auto-detect: General Exception on {port_device} for {scale.name}: {str(e)}")
                if ser and ser.is_open:
                    ser.close()
                    
    return False, f"Could not connect to scale {scale.name}. No suitable COM port found or scale not responsive."


def _connect_scale_mettler(scale):
    """
    Mettler Toledo connection mode: uses configurable serial parameters,
    exclusive port access, and a defaults-fallback strategy.
    Does NOT attempt to read data — that's deferred to get_weight().
    """
    ser = None
    timeout = scale.timeout or 1
    
    ports_to_try = []
    if scale.com_port:
        ports_to_try.append(scale.com_port)

    # Attempt to connect to the specified port first
    for port in ports_to_try:
        print(f"[MT] Attempting to connect to specified port: {port} for scale {scale.name}")
        
        try:
            ser = open_serial_for_scale(scale, port=port, timeout=timeout, exclusive=True)
            if ser.is_open:
                print(f"[MT] Successfully opened port {port} for scale {scale.name}.")
                ser.close()
                return True, f"Successfully connected to {scale.name} on {port}."
        except serial.SerialException as e:
            print(f"[MT] SerialException on port {port} for scale {scale.name}: {str(e)}")
            if ser and ser.is_open:
                ser.close()
        except Exception as e:
            print(f"[MT] General Exception on port {port} for scale {scale.name}: {str(e)}")
            if ser and ser.is_open:
                ser.close()
    # If specified port failed or was not provided, attempt auto-detection
    print(f"[MT] Specified port connection failed or port not set for {scale.name}. Attempting auto-detection.")
    available_comports = serial.tools.list_ports.comports()
    print(f"[MT] Available COM ports for auto-detection: {[p.device for p in available_comports]}")

    for comport_info in available_comports:
        port_device = comport_info.device
        if scale.com_port and port_device == scale.com_port:
            continue

        if 'TTYUSB' in port_device.upper() or 'COM' in port_device.upper() or 'SERIAL' in port_device.upper():
            print(f"[MT] Auto-detect: Trying port {port_device} for scale {scale.name}")
            try:
                ser = open_serial_for_scale(scale, port=port_device, timeout=timeout, exclusive=True)
                if ser.is_open:
                    print(f"[MT] Auto-detect: Successfully opened port {port_device} for scale {scale.name}.")
                    ser.close()
                    scale.com_port = port_device
                    return True, f"Successfully connected to {scale.name} on {port_device} (auto-detected)."
            except serial.SerialException as e:
                print(f"[MT] Auto-detect: SerialException on {port_device} for {scale.name}: {str(e)}")
                if ser and ser.is_open:
                    ser.close()
            except Exception as e:
                print(f"[MT] Auto-detect: General Exception on {port_device} for {scale.name}: {str(e)}")
                if ser and ser.is_open:
                    ser.close()
    return False, f"Could not connect to scale {scale.name}. No suitable COM port found or scale not responsive."


@login_required
def get_weight(request, scale_id):
    if request.method == 'POST':
        try:
            scale = get_object_or_404(Scale, pk=scale_id)
            satellite_response = _get_satellite_cached_weight(scale)
            if satellite_response is not None:
                return satellite_response
            
            # Check if scale is connected
            if scale.last_connection_status != "connected":
                return JsonResponse({
                    'success': False,
                })
            
            # Try to read from the scale
            ser = None
            try:
                if scale.mettler_toledo:
                    ser = open_serial_for_scale(scale, timeout=scale.timeout or 1)
                    
                    if ser.is_open:
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
                else:
                    ser = open_serial_for_scale(scale, timeout=scale.timeout or 2)
                    if ser.is_open:
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
        
        # Get tare weight from active product (if one exists)
        from ..models import Product
        
        tare_weight = 0
        active_product = Product.objects.filter(is_active=True).first()
        if active_product and active_product.tare_weight:
            tare_weight = float(active_product.tare_weight)
        
        satellite_response = _get_satellite_cached_weight(scale, tare_weight=tare_weight)
        if satellite_response is not None:
            return satellite_response
        
        # Fallback: direct serial read (original behaviour when satellite is off)
        if not scale.com_port:
            return JsonResponse({
                'success': False,
                'message': f'Scale {scale.name} does not have a COM port configured'
            }, status=400)
        
        ser = None
        try:
            if scale.mettler_toledo:
                ser = open_serial_for_scale(scale, timeout=scale.timeout or 1)
                
                if ser.is_open:
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
                        net_weight = weight - tare_weight
                        return JsonResponse({
                            'success': True,
                            'weight': net_weight,
                            'gross_weight': weight,
                            'scale_id': str(scale.scale_id) if scale.scale_id else None,
                            'scale_name': scale.name
                        })
                    else:
                        return JsonResponse({
                            'success': False,
                            'message': f'No numeric weight found in: {raw_str}'
                        })
            else:
                ser = open_serial_for_scale(scale, timeout=scale.timeout or 2)
                if ser.is_open:
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
                        net_weight = weight - tare_weight
                        # Extract unit if present
                        unit_match = re.search(r'(kg|g|lbs|lb|pd)\b', raw_str, re.IGNORECASE)
                        unit = unit_match.group(1).lower() if unit_match else 'kg'
                        
                        return JsonResponse({
                            'success': True,
                            'weight': net_weight,
                            'gross_weight': weight,
                            'unit': unit,
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
