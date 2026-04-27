"""
Scale management views for IntelliScale.
Handles scale CRUD, connection, and weight reading.
"""
import json
import logging
import re
import time
import serial
import serial.tools.list_ports
import redis
from django.conf import settings as django_settings
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from users.views import is_admin
from ..models import Scale, ScaleIdHistory
from ..forms import ScaleForm
from ..satellite_service import (
    channel_for_scale_key,
    get_cached_weight_by_scale_id,
)
from ..scale_utils import (
    open_serial_for_scale,
    uses_cas_stream_protocol,
    uses_mettler_protocol,
)

logger = logging.getLogger(__name__)

FRESH_WEIGHT_TIMEOUT_SECONDS = 1.5


def _cache_key_for(scale):
    return str(scale.scale_id) if scale.scale_id else str(scale.pk)


def _wait_for_fresh_sample(scale, timeout=FRESH_WEIGHT_TIMEOUT_SECONDS):
    """Block until the satellite publishes the next reading for this scale.

    Returns the payload dict, or None if Redis is unreachable / no message
    arrives in time.
    """
    cache_key = _cache_key_for(scale)
    redis_url = getattr(django_settings, 'CELERY_BROKER_URL', 'redis://localhost:6379/0')
    pubsub = None
    try:
        client = redis.Redis.from_url(redis_url, decode_responses=True)
        pubsub = client.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(channel_for_scale_key(cache_key))
    except redis.RedisError as exc:
        logger.warning(f"get_weight could not subscribe to Redis: {exc}")
        return None

    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            message = pubsub.get_message(timeout=min(remaining, 0.5))
            if message and message.get('type') == 'message':
                try:
                    return json.loads(message['data'])
                except (ValueError, TypeError):
                    continue
        return None
    finally:
        try:
            pubsub.close()
        except Exception:
            pass


def _payload_to_response(scale, payload, tare_weight=0):
    """Translate a satellite cache/pubsub payload into the JSON shape expected
    by the existing weight endpoints.
    """
    if not payload:
        return JsonResponse({
            'success': False,
            'message': (
                f'No weight available for {scale.name}. '
                'Ensure the satellite service is running (python manage.py run_satellite).'
            )
        })

    if 'weight' not in payload or 'timestamp' not in payload:
        return JsonResponse({
            'success': False,
            'message': payload.get('last_error') or f'{scale.name} has not produced a valid weight yet.',
            'source': 'satellite',
            'status': payload.get('status', 'unknown'),
            'last_attempt': payload.get('last_attempt'),
            'last_error': payload.get('last_error'),
        })

    gross_weight = payload['weight']
    response = {
        'success': True,
        'weight': gross_weight - tare_weight if tare_weight else gross_weight,
        'gross_weight': gross_weight,
        'unit': payload.get('unit', 'kg'),
        'scale_id': payload.get('scale_id'),
        'scale_name': payload.get('scale_name'),
        'source': 'satellite',
        'cache_age_seconds': round(time.time() - payload['timestamp'], 2),
        'status': payload.get('status', 'ok'),
        'last_attempt': payload.get('last_attempt', payload['timestamp']),
        'last_error': payload.get('last_error'),
    }
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
    if uses_mettler_protocol(scale) or uses_cas_stream_protocol(scale):
        return _connect_scale_passive(scale)
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


def _connect_scale_passive(scale):
    """
    Passive connection mode: uses configurable serial parameters,
    exclusive port access, and a defaults-fallback strategy without
    sending any probe bytes to the scale.
    Does NOT attempt to read data — that's deferred to get_weight().
    """
    ser = None
    timeout = scale.timeout or 1
    
    ports_to_try = []
    if scale.com_port:
        ports_to_try.append(scale.com_port)

    # Attempt to connect to the specified port first
    for port in ports_to_try:
        print(f"[PASSIVE] Attempting to connect to specified port: {port} for scale {scale.name}")
        
        try:
            ser = open_serial_for_scale(scale, port=port, timeout=timeout, exclusive=True)
            if ser.is_open:
                print(f"[PASSIVE] Successfully opened port {port} for scale {scale.name}.")
                ser.close()
                return True, f"Successfully connected to {scale.name} on {port}."
        except serial.SerialException as e:
            print(f"[PASSIVE] SerialException on port {port} for scale {scale.name}: {str(e)}")
            if ser and ser.is_open:
                ser.close()
        except Exception as e:
            print(f"[PASSIVE] General Exception on port {port} for scale {scale.name}: {str(e)}")
            if ser and ser.is_open:
                ser.close()
    # If specified port failed or was not provided, attempt auto-detection
    print(f"[PASSIVE] Specified port connection failed or port not set for {scale.name}. Attempting auto-detection.")
    available_comports = serial.tools.list_ports.comports()
    print(f"[PASSIVE] Available COM ports for auto-detection: {[p.device for p in available_comports]}")

    for comport_info in available_comports:
        port_device = comport_info.device
        if scale.com_port and port_device == scale.com_port:
            continue

        if 'TTYUSB' in port_device.upper() or 'COM' in port_device.upper() or 'SERIAL' in port_device.upper():
            print(f"[PASSIVE] Auto-detect: Trying port {port_device} for scale {scale.name}")
            try:
                ser = open_serial_for_scale(scale, port=port_device, timeout=timeout, exclusive=True)
                if ser.is_open:
                    print(f"[PASSIVE] Auto-detect: Successfully opened port {port_device} for scale {scale.name}.")
                    ser.close()
                    scale.com_port = port_device
                    return True, f"Successfully connected to {scale.name} on {port_device} (auto-detected)."
            except serial.SerialException as e:
                print(f"[PASSIVE] Auto-detect: SerialException on {port_device} for {scale.name}: {str(e)}")
                if ser and ser.is_open:
                    ser.close()
            except Exception as e:
                print(f"[PASSIVE] Auto-detect: General Exception on {port_device} for {scale.name}: {str(e)}")
                if ser and ser.is_open:
                    ser.close()
    return False, f"Could not connect to scale {scale.name}. No suitable COM port found or scale not responsive."


@login_required
def get_weight(request, scale_id):
    """Force a fresh weight reading by waiting for the next satellite sample.

    This blocks the request until the satellite publishes a new pub/sub message
    for this scale (up to FRESH_WEIGHT_TIMEOUT_SECONDS). Falls back to the
    cached snapshot if Redis is unreachable.
    """
    if request.method != 'POST':
        return JsonResponse({
            'success': False,
            'message': 'Only POST requests are allowed.'
        })

    try:
        scale = get_object_or_404(Scale, pk=scale_id)
        payload = _wait_for_fresh_sample(scale)
        if payload is None:
            payload = get_cached_weight_by_scale_id(_cache_key_for(scale))
        return _payload_to_response(scale, payload)
    except Exception as e:
        logger.exception(f"get_weight error for scale {scale_id}: {e}")
        return JsonResponse({'success': False, 'message': str(e)})


@csrf_exempt
def get_current_weight_api(request, scale_id):
    """External API endpoint — returns the cached snapshot (no force-fresh)."""
    try:
        try:
            scale = Scale.objects.get(scale_id=str(scale_id))
        except Scale.DoesNotExist:
            return JsonResponse({
                'success': False,
                'message': f'Scale not found with scale_id: {scale_id}'
            }, status=404)

        from ..models import Product
        tare_weight = 0
        active_product = Product.objects.filter(is_active=True).first()
        if active_product and active_product.tare_weight:
            tare_weight = float(active_product.tare_weight)

        payload = get_cached_weight_by_scale_id(_cache_key_for(scale))
        return _payload_to_response(scale, payload, tare_weight=tare_weight)
    except Exception as e:
        logger.exception(f"get_current_weight_api error for scale {scale_id}: {e}")
        return JsonResponse({'success': False, 'message': str(e)})
