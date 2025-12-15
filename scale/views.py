from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.http import HttpResponseForbidden, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from users.views import is_admin 
from .models import Scale, WeighingProcess, Product, DeliveryNote, WeighingRecord, CompanySettings, Driver, Truck, Trailer, ScaleIdHistory, PrintingNote, PrintingRecord
from .forms import ScaleForm, WeighingProcessForm, ProductForm, DeliveryNoteForm, CompanySettingsForm, DriverForm, TruckForm, TrailerForm
import serial
import serial.tools.list_ports
from django.utils import timezone
import json
from decimal import Decimal
from django.core.paginator import Paginator
import requests
import random
import xmlrpc.client
import socket
from datetime import datetime
import re
import time
import logging

from .tasks import sync_odoo_delivery_notes

logger = logging.getLogger(__name__)

# Scale Management Views
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
    ser = None
    baud_rate = 9600  # Standard baud rate
    timeout = 2       # Connection timeout in seconds
    
    ports_to_try = []
    if scale.com_port:
        ports_to_try.append(scale.com_port)

    # Attempt to connect to the specified port first
    for port in ports_to_try:
        print(f"Attempting to connect to specified port: {port} for scale {scale.name}")
        try:
            ser = serial.Serial(port, baud_rate, timeout=timeout)
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
                ser = serial.Serial(port_device, baud_rate, timeout=timeout)
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


###################################################################################################
#  Get Weight
@login_required
def get_weight(request, scale_id):
    if request.method == 'POST':
        try:
            scale = get_object_or_404(Scale, pk=scale_id)
            
            # Check if scale is connected
            if scale.last_connection_status != "connected":
                return JsonResponse({
                    'success': False,
                    # 'message': 'Scale is not connected. Please connect the scale first.'
                })
            
            # Try to read from the scale
            ser = None
            try:
                ser = serial.Serial(scale.com_port, 9600, timeout=2)
                if ser.is_open:
                    #clear any stale data from buffer
                    for _ in range(3):
                        ser.reset_input_buffer()
                        time.sleep(0.05)
                    # Send command to get weight (this may vary by scale model)
                    ser.write(b"\r\n")  # Some scales need a CR/LF to trigger reading
                    # Read response
                    time.sleep(0.3)
                    line = ser.readline()
                    # Decode bytes
                    try:
                        decoded = line.decode('utf-8', errors='ignore').strip()
                    except Exception:
                        decoded = line.decode(errors='ignore').strip()

                    print(f"Raw data from scale {scale.name}: '{decoded}' (bytes: {line})")

                    # Attempt to parse numeric weight from decoded string
                    # robustly looks for a number which may include decimal points or commas instead of using slices 
                    numeric_match = re.search(r'([-+]?\d+(?:[.,]\d+)?)\s*(kg|g|lbs|lb|pd)\b', decoded, re.IGNORECASE)

                    if numeric_match:
                        num_str = numeric_match.group(1).replace(',', '')
                        #not in use for now - holds the weights UOM
                        unit = numeric_match.group(2).lower()
                        try:
                            weight = float(num_str)
                            print(f"Successfully parsed weight for scale {scale.name}: {weight}")
                            return JsonResponse({
                                'success': True,
                                'weight': weight
                            })
                        except ValueError:
                            print(f"Could not parse numeric weight from '{num_str}' for scale {scale.name}")
                            return JsonResponse({
                                'success': False,
                                'message': f'Could not parse numeric weight: {num_str}'
                            })
                    else:
                        print(f"No numeric weight found in '{decoded}' for scale {scale.name}")
                        return JsonResponse({
                            'success': False,
                            'message': f'No numeric weight found in: {decoded}'
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



#################################################################################################
# Weighing Process Management Views
@login_required
@user_passes_test(is_admin)
def weighing_process_list(request):
    weighing_processes = WeighingProcess.objects.all().order_by('name')
    return render(request, 'scale/weighing_process_list.html', {'weighing_processes': weighing_processes})

@login_required
@user_passes_test(is_admin)
def weighing_process_detail(request, pk):
    weighing_process = get_object_or_404(WeighingProcess, pk=pk)
    return render(request, 'scale/weighing_process_detail.html', {'weighing_process': weighing_process})

@login_required
@user_passes_test(is_admin)
def weighing_process_create(request):
    if request.method == 'POST':
        form = WeighingProcessForm(request.POST)
        if form.is_valid():
            weighing_process = form.save()
            messages.success(request, f'Weighing process {weighing_process.name} was created successfully.')
            return redirect('scale:weighing_process_list')
    else:
        form = WeighingProcessForm()
    
    return render(request, 'scale/weighing_process_create.html', {'form': form})

@login_required
@user_passes_test(is_admin)
def weighing_process_edit(request, pk):
    weighing_process = get_object_or_404(WeighingProcess, pk=pk)
    
    if request.method == 'POST':
        form = WeighingProcessForm(request.POST, instance=weighing_process)
        if form.is_valid():
            weighing_process = form.save()
            messages.success(request, f'Weighing process {weighing_process.name} was updated successfully.')
            return redirect('scale:weighing_process_detail', pk=weighing_process.pk)
    else:
        form = WeighingProcessForm(instance=weighing_process)
    
    return render(request, 'scale/weighing_process_edit.html', {'form': form, 'weighing_process': weighing_process})

@login_required
@user_passes_test(is_admin)
def weighing_process_delete(request, pk):
    weighing_process = get_object_or_404(WeighingProcess, pk=pk)
    
    if request.method == 'POST':
        name = weighing_process.name
        weighing_process.delete()
        messages.success(request, f'Weighing process {name} was deleted successfully.')
        return redirect('scale:weighing_process_list')
    
    # If not POST, redirect to detail page
    return redirect('scale:weighing_process_detail', pk=pk)


# Weighing Station Views
@login_required
# @user_passes_test(is_admin)
def weighing_station(request):
    scales = Scale.objects.filter(is_active=True).order_by('name')
    products = Product.objects.filter(is_active=True).order_by('name')
    processes = WeighingProcess.objects.filter(is_active=True).order_by('name')
    delivery_notes = DeliveryNote.objects.filter(status='Open').order_by('-created_at')[:20]  # Get the 20 most recent open delivery notes
    
    # Get data for delivery note creation modal
    drivers = Driver.objects.all().order_by('name')
    trucks = Truck.objects.all().order_by('license_plate')
    trailers = Trailer.objects.all().order_by('license_plate')
    
    #Get Min and Max Weights from active process
    min_weight = None
    max_weight = None
    allow_manual_entry = False
    if processes.exists():
        # Get from the first process (or you could use specific logic to choose which process)
        active_process = processes.first()
        min_weight = active_process.min_weight
        max_weight = active_process.max_weight
        allow_manual_entry = active_process.allow_manual_entry
        weight_rounding = active_process.weight_rounding
    
    # print('Min Weight: ', min_weight)
    # print('Max Weight: ', max_weight)
    # print('Allow Manual Entry: ', allow_manual_entry)
    # print('Weight Rounding: ', weight_rounding)
    # Get custom fields data for all processes
    process_custom_fields = {}
    for process in processes:
        process_custom_fields[process.id] = process.custom_fields_schema

    
    if request.method == 'POST':
        
        try:
            # Extract form data
            scale_id = request.POST.get('scale_id')
            product_id = request.POST.get('product_id')
            process_id = request.POST.get('process_id')
            
            # Handle missing product_id - get default active product
            if not product_id:
                active_products = Product.objects.filter(is_active=True)
                if active_products.exists():
                    product_id = active_products.first().id
                else:
                    messages.error(request, 'No active product available. Please activate a product first.')
                    return redirect('scale:weighing_station')
            gross_weight = request.POST.get('gross_weight', '0')
            tare_weight = request.POST.get('tare_weight', '0')
            net_weight = request.POST.get('net_weight', '0')
            unit_of_measure = request.POST.get('unit_of_measure', 'kg')
            notes = request.POST.get('notes', '')
            
            # Handle barcode stripping based on process settings
            raw_barcode = request.POST.get('barcode', '')
            process_id = request.POST.get('process_id')
            process = None
            allow_spaces = False
            
            if process_id:
                process = WeighingProcess.objects.get(pk=process_id)
                allow_spaces = process.allow_spaces_in_barcode
                
            if allow_spaces:
                barcode = raw_barcode # Don't strip if spaces are allowed
            else:
                barcode = raw_barcode.strip()
                
            weighing_record_id = request.POST.get('weighing_record_id') # Get the ID for update

            # Round net weight to the nearest weight_rounding
            # net_weight = round(float(net_weight), weight_rounding)

            print('Net Weight: ', net_weight)
            
            # Convert weights to Decimal, handling empty strings
            gross_weight = Decimal(gross_weight) if gross_weight and gross_weight.strip() else Decimal('0')
            tare_weight = Decimal(tare_weight) if tare_weight and tare_weight.strip() else Decimal('0')
            net_weight = Decimal(net_weight) if net_weight and net_weight.strip() else Decimal('0')
            
            if net_weight < min_weight or net_weight > max_weight:
                messages.error(request, f'Net weight must be between {min_weight} and {max_weight} kg.')
                return redirect('scale:weighing_station')
            
            # Extract custom fields
            custom_data = {}
            for key, value in request.POST.items():
                if key.startswith('custom_field_'):
                    field_name = key.replace('custom_field_', '')
                    custom_data[field_name] = value
            
            # If the process allows marshalling, ensure marshalling-specific fields are handled properly
            if process.allow_marshalling:
                # For marshalling, we might want to enhance the custom_data with
                # additional validation or processing for lot_number and group_number
                pass
            
            # Handle delivery note association
            delivery_note = None
            delivery_note_id = request.POST.get('delivery_note_id')
            # process is already fetched above
            
            # Handle CTL Workflow logic
            if process.process_type in ['ctl_workflow', 'ctl_commercial_workflow']:
                with transaction.atomic():
                    # Check if we have an active delivery note from the form
                    if delivery_note_id:
                        # Use the active delivery note from the form, and lock the row for update
                        delivery_note = get_object_or_404(DeliveryNote.objects.select_for_update(), pk=delivery_note_id)
                        
                        # Check if delivery note is in 'checked' state for ctl_workflow
                        if process.process_type == 'ctl_workflow' and delivery_note.get_state() != 'checked':
                            messages.error(request, f'Delivery Note {delivery_note.delivery_note_number} has not been checked (Status: {delivery_note.get_state()}).')
                            return redirect('scale:weighing_station')
                        
                        # Verify this barcode belongs to this delivery note
                        if barcode:
                            if delivery_note.has_barcode_been_scanned(barcode, allow_spaces=allow_spaces):
                                # This is a rescan/update, allow it to proceed
                                pass
                            elif not delivery_note.can_accept_barcode(barcode, allow_spaces=allow_spaces):
                                # If allow_bale_insert is enabled, allow unfamiliar barcodes
                                if process.allow_bale_insert:
                                    # This is a new barcode for a delivery note with bale insertion enabled
                                    # Allow it to proceed - it will be created in the ERP system
                                    pass
                                else:
                                    # This is a new scan, but it's not valid
                                    # Check if this is just a rescan of an already-scanned barcode from the same delivery note
                                    if delivery_note.is_scanning_complete() and delivery_note.find_bale_by_barcode(barcode, allow_spaces=allow_spaces):
                                        # This barcode belongs to this delivery note, allow rescan/update
                                        pass
                                    elif delivery_note.is_scanning_complete():
                                        messages.error(request, f'Delivery note {delivery_note.delivery_note_number} is already complete.')
                                    else:
                                        messages.error(request, f'Barcode {barcode} not found in delivery note {delivery_note.delivery_note_number}.')
                                    return redirect('scale:weighing_station')
                            
                    elif barcode:
                        # No active delivery note, search by barcode
                        delivery_notes_with_barcode = DeliveryNote.objects.all()
                        found_delivery_note = None
                        
                        for dnote in delivery_notes_with_barcode:
                            if dnote.find_bale_by_barcode(barcode, allow_spaces=allow_spaces):
                                found_delivery_note = dnote
                                break
                        
                        if found_delivery_note:
                            # Lock the found delivery note for update
                            delivery_note = DeliveryNote.objects.select_for_update().get(pk=found_delivery_note.pk)

                            # Check if delivery note is in 'checked' state for ctl_workflow
                            if process.process_type == 'ctl_workflow' and delivery_note.get_state() != 'checked':
                                messages.error(request, f'Delivery Note {delivery_note.delivery_note_number} has not been checked (Status: {delivery_note.get_state()}).')
                                return redirect('scale:weighing_station')

                            # Check if this delivery note can accept this barcode
                            if not delivery_note.can_accept_barcode(barcode, allow_spaces=allow_spaces):
                                # Check if this is just a rescan of an already-scanned barcode from the same delivery note
                                if delivery_note.is_scanning_complete() and delivery_note.find_bale_by_barcode(barcode, allow_spaces=allow_spaces):
                                    # This barcode belongs to this delivery note, allow rescan/update
                                    # But only if the delivery note is currently being scanned
                                    if not delivery_note.is_being_scanned:
                                        messages.error(request, f'Cannot recall and update. This bale has already been scanned for delivery note {delivery_note.delivery_note_number}.')
                                        return redirect('scale:weighing_station')
                                elif delivery_note.is_scanning_complete():
                                    messages.error(request, f'Delivery note {delivery_note.delivery_note_number} is already complete.')
                                    return redirect('scale:weighing_station')
                                # Allow re-scans by checking this after can_accept_barcode
                                elif delivery_note.has_barcode_been_scanned(barcode, allow_spaces=allow_spaces):
                                    # This is a re-scan, so we allow it, but only if delivery note is already being scanned
                                    if not delivery_note.is_being_scanned:
                                        messages.error(request, f'Cannot recall and update. This bale has already been scanned for delivery note {delivery_note.delivery_note_number}.')
                                        return redirect('scale:weighing_station')
                                else:
                                    messages.error(request, f'Barcode {barcode} not found in delivery note {delivery_note.delivery_note_number}.')
                                    return redirect('scale:weighing_station')
                            else:
                                # If the delivery note is not being scanned and the barcode can be accepted,
                                # then activate this delivery note for scanning
                                # Activate this delivery note for scanning if not already active
                                currently_scanned = DeliveryNote.objects.filter(is_being_scanned=True).exclude(pk=delivery_note.pk).first()
                                if currently_scanned:
                                    messages.error(request, f'Another delivery note ({currently_scanned.delivery_note_number}) is currently being scanned.')
                                    return redirect('scale:weighing_station')
                                
                                if not delivery_note.is_being_scanned:
                                    # Deactivate any other delivery notes
                                    DeliveryNote.objects.filter(is_being_scanned=True).update(is_being_scanned=False)
                                    delivery_note.is_being_scanned = True
                                    delivery_note.save()
                            
                        else:
                            messages.error(request, 'Barcode not found. Please scan the correct bale.')
                            return redirect('scale:weighing_station')
                    else:
                        messages.error(request, 'No barcode provided for CTL workflow.')
                        return redirect('scale:weighing_station')
            elif delivery_note_id:
                delivery_note = get_object_or_404(DeliveryNote, pk=delivery_note_id)
            
            print('------FINAL--------------------------')
            print('Net Weight: ', net_weight)
            print('Gross Weight: ', gross_weight)
            print('Tare Weight: ', tare_weight)
            
            # Check if this is a weighbridge process and handle existing records
            weighing_record = None
            
            if process.process_type == 'WeighBridge' and delivery_note:
                # Look for existing weighing record in this delivery note that has gross weight but no tare weight
                existing_record = WeighingRecord.objects.filter(
                    delivery_note=delivery_note,
                    gross_weight__gt=0,  # Has gross weight
                    tare_weight__isnull=True  # No tare weight yet
                ).first()
                
                # Also check for records with tare_weight = 0
                if not existing_record:
                    existing_record = WeighingRecord.objects.filter(
                        delivery_note=delivery_note,
                        gross_weight__gt=0,  # Has gross weight
                        tare_weight=0  # Tare weight is 0
                    ).first()
                
                if existing_record:
                    # Update existing record with tare weight (current weight becomes tare)
                    existing_record.tare_weight = gross_weight  # Current weight reading becomes tare
                    existing_record.net_weight = existing_record.gross_weight - existing_record.tare_weight
                    existing_record.user = request.user  # Update user who performed tare weighing
                    existing_record.notes = notes if notes else existing_record.notes  # Update notes if provided
                    existing_record.save()
                    weighing_record = existing_record
                    
                    # Close the delivery note after second weighing (tare) is complete
                    delivery_note.status = 'Closed'
                    delivery_note.save()
                    
                    messages.success(request, f'Tare weight updated for existing weighing record in delivery note {delivery_note.delivery_note_number}. Delivery note has been closed.')
                else:
                    weighing_record = WeighingRecord.objects.create(
                        scale=scale,
                        weighing_scale_id=scale.scale_id,
                        product_id=product_id,
                        process_id=process_id,
                        user=request.user,
                        gross_weight=gross_weight,
                        tare_weight=tare_weight,
                        net_weight=net_weight,
                        unit_of_measure=unit_of_measure,
                        notes=notes,
                        custom_data=custom_data,
                        delivery_note=delivery_note,
                        barcode=barcode
                    )
            else:
                # For CTL workflow, check for existing record to update. Otherwise, create new.
                existing_record = None
                if process.process_type in ['ctl_workflow', 'ctl_commercial_workflow'] and delivery_note and barcode:
                    existing_record = WeighingRecord.objects.filter(barcode=barcode, delivery_note=delivery_note).first()
                
                if existing_record:
                    # Update existing record BEFORE calling ERP
                    existing_record.scale = get_object_or_404(Scale, pk=scale_id)
                    existing_record.weighing_scale_id = existing_record.scale.scale_id
                    existing_record.product = get_object_or_404(Product, pk=product_id)
                    existing_record.process = process
                    existing_record.user = request.user
                    existing_record.gross_weight = gross_weight
                    existing_record.tare_weight = tare_weight
                    existing_record.net_weight = net_weight
                    existing_record.unit_of_measure = unit_of_measure
                    existing_record.notes = notes
                    
                    # If marshalling is allowed, preserve existing marshalling fields (lot_number, group_number)
                    # unless they are being explicitly updated in the current request
                    if existing_record.custom_data and isinstance(existing_record.custom_data, dict):
                        existing_record.custom_data.update(custom_data)
                    else:
                        existing_record.custom_data = custom_data
                    
                    existing_record.is_synced = False # Mark as not synced initially
                    existing_record.save()
                    
                    # Call ERP with the updated record
                    erp_success = send_to_erp(barcode, net_weight, existing_record.weighing_scale_id, existing_record.id, request, existing_record.custom_data, process.process_type, process.id, delivery_note)
                    
                    if erp_success:
                        existing_record.is_synced = True  # Mark as synced since ERP call succeeded
                        existing_record.save()
                        weighing_record = existing_record
                        messages.success(request, f'Weight for bale {barcode} updated successfully.')
                    else:
                        # Record already updated with error message (saved by send_to_erp)
                        # Reload to ensure we have the latest error message
                        existing_record.refresh_from_db()
                        error_message = existing_record.sync_error_message or "Unknown error occurred during ERP communication"
                        messages.error(request, f'Error sending to ERP: {error_message}')
                        return redirect('scale:weighing_station')
                else:
                    # Create new record BEFORE calling ERP so we can save errors
                    scale = get_object_or_404(Scale, pk=scale_id)
                    
                    # Create the record immediately
                    weighing_record = WeighingRecord.objects.create(
                        scale=scale,
                        weighing_scale_id=scale.scale_id,
                        product_id=product_id,
                        process_id=process_id,
                        user=request.user,
                        gross_weight=gross_weight,
                        tare_weight=tare_weight,
                        net_weight=net_weight,
                        unit_of_measure=unit_of_measure,
                        notes=notes,
                        custom_data=custom_data,
                        delivery_note=delivery_note,
                        barcode=barcode,
                        is_synced=False # Start as not synced
                    )
                    
                    # Call ERP with the new record ID
                    erp_success = send_to_erp(barcode, net_weight, weighing_record.weighing_scale_id, weighing_record.id, request, weighing_record.custom_data, process.process_type, process.id, delivery_note)
                    
                    if erp_success:
                        # Mark as synced
                        weighing_record.is_synced = True
                        weighing_record.save()
                        messages.success(request, 'Weighing record created successfully.')
                    else:
                        # Record already exists with error message (saved by send_to_erp)
                        # Reload to ensure we have the latest error message
                        weighing_record.refresh_from_db()
                        error_message = weighing_record.sync_error_message or "Unknown error occurred during ERP communication"
                        messages.error(request, f'Error sending to ERP: {error_message}')
                        return redirect('scale:weighing_station')
            
            # Handle CTL Workflow completion logic
            if process.process_type in ['ctl_workflow', 'ctl_commercial_workflow'] and weighing_record and delivery_note:
                # Add barcode to scanned list (this handles increment automatically)
                if delivery_note.add_scanned_barcode(barcode, allow_spaces=allow_spaces):
                    delivery_note.save()
                    # messages.success(request, f'Bale {barcode} scanned successfully.')
                else:
                    # This shouldn't happen due to can_accept_barcode check, but just in case
                    # messages.warning(request, f'Bale {barcode} was already scanned.')
                    pass
                
                # Check if scanning is complete
                if delivery_note.is_scanning_complete():
                    # Do not set is_being_scanned = False here. User must confirm closure.
                    delivery_note.save()
                    # messages.success(request, f'Delivery note {delivery_note.delivery_note_number} scanning completed! All {delivery_note.get_bale_count()} bales have been scanned. Please confirm closure.')
                else:
                    remaining = delivery_note.get_remaining_bales_count()
                    # messages.success(request, f'Bale scanned successfully. {remaining} bales remaining for delivery note {delivery_note.delivery_note_number}.')
                    pass
            
            print_after_save = request.POST.get('print_after_save') == 'true'
            
            if print_after_save:
                # Update print count
                weighing_record.print_count += 1
                weighing_record.last_printed_at = timezone.now()
                weighing_record.save()
                
                # Add a message indicating that printing was triggered
                # messages.success(request, 'Weighing record created and sent to printer.')
                pass
                
                # TODO: Implement actual printing functionality
                # This would typically involve generating a PDF and sending it to a printer
                # For now, just redirect to a print view or mock this functionality
            else:
                # Message already set above based on ERP success/failure
                pass
                
            return redirect('scale:weighing_station')
            
        except Exception as e:
            messages.error(request, f'Error creating weighing record: {str(e)}')
    
    # Get currently active delivery note for CTL workflow
    active_delivery_note = DeliveryNote.objects.filter(is_being_scanned=True).first()
    
    if active_delivery_note:
        active_delivery_note.scanned_records_data = active_delivery_note.get_scanned_records_data()
        
        # Determine the appropriate bale count to display based on the active process
        # If there are processes and the first one allows bale insert, use delivered bale count
        active_process = processes.first() if processes.exists() else None
        if active_process and active_process.allow_bale_insert:
            # When bale insert is allowed, use the delivered bale count instead of the original total
            active_delivery_note.display_bale_count = active_delivery_note.get_bale_count_delivered()
        else:
            # Otherwise, use the standard bale count
            active_delivery_note.display_bale_count = active_delivery_note.get_bale_count()
    else:
        # Set a default value when there's no active delivery note
        active_delivery_note = None
    
    # Create a dictionary of product tare weights for the template
    product_tare_weights = {}
    for product in products:
        product_tare_weights[product.id] = float(product.tare_weight or 0)
    
    # Create a dictionary of marshalling allowance for each process
    process_marshalling = {}
    for process in processes:
        process_marshalling[process.id] = process.allow_marshalling
    
    process_allow_bale_insert = {}
    process_allow_spaces_in_barcode = {}
    process_use_code39_mod43_validation = {}
    process_rolling_hessian_config = {}
    for process in processes:
        process_allow_bale_insert[process.id] = process.allow_bale_insert
        process_allow_spaces_in_barcode[process.id] = process.allow_spaces_in_barcode
        process_use_code39_mod43_validation[process.id] = process.use_code39_mod43_validation
        process_rolling_hessian_config[process.id] = process.rolling_hessian

    # Rolling Hessian Logic
    prefilled_hessian_value = ''
    if active_process and active_process.rolling_hessian and active_delivery_note:
        # Get the latest weighing record for this delivery note
        last_record = WeighingRecord.objects.filter(
            delivery_note=active_delivery_note
        ).order_by('-timestamp').first()
        
        if last_record and last_record.custom_data:
            # Check for 'hessian_id'
            if 'hessian_id' in last_record.custom_data:
                prefilled_hessian_value = last_record.custom_data['hessian_id']
    
    context = {
        'scales': scales,
        'products': products,
        'processes': processes,
        'delivery_notes': delivery_notes,
        'drivers': drivers,
        'trucks': trucks,
        'trailers': trailers,
        'process_custom_fields': json.dumps(process_custom_fields),
        'process_marshalling': json.dumps(process_marshalling),
        'process_allow_bale_insert': json.dumps(process_allow_bale_insert),
        'process_allow_spaces_in_barcode': json.dumps(process_allow_spaces_in_barcode),
        'process_use_code39_mod43_validation': json.dumps(process_use_code39_mod43_validation),
        'process_rolling_hessian_config': json.dumps(process_rolling_hessian_config),
        'unsynced_count': WeighingRecord.objects.filter(is_synced=False).count(),
        'allow_manual_entry': allow_manual_entry,
        'active_delivery_note': active_delivery_note,
        'product_tare_weights': json.dumps(product_tare_weights),
        'prefilled_hessian_value': prefilled_hessian_value,
    }
    
    return render(request, 'scale/weighing_station.html', context)


def send_to_erp(barcode, net_weight, scale_id, weighing_record_id, request, custom_data, process_type=None, process_id=None, delivery_note=None):
    # Trim whitespace from barcode unless process allows spaces
    allow_spaces = False
    if process_id:
        try:
            process = WeighingProcess.objects.get(pk=process_id)
            allow_spaces = process.allow_spaces_in_barcode
        except WeighingProcess.DoesNotExist:
            pass
            
    if allow_spaces:
        barcode = str(barcode) if barcode else ''
    else:
        barcode = str(barcode).strip() if barcode else ''

        
    
    # TODO: Implement actual sending to erp system
    
    # Get company settings
    company_settings = CompanySettings.objects.first()
    
    # Get API key from company settings
    api_key = company_settings.api_key if company_settings and company_settings.api_key else None
    # print('Company Settings: ', company_settings)
    # print('Company Settings ERP System: ', company_settings.erp_system.name)
    # print('Password: ', company_settings.erp_password)
    # print('Username: ', company_settings.erp_username)
    # print('API URL: ', company_settings.api_url)
    # print('Database Name: ', company_settings.database_name)
    
    # Parse URL into host and port
    # try:
    #     # Remove protocol prefix if present
    #     host = company_settings.api_url.replace('http://', '').replace('https://', '')
    #     # Split host and port (default to 80 if no port specified)
    #     if ':' in host:
    #         host, port = host.split(':')
    #         port = int(port)
    #     else:
    #         port = 80
            
    #     if socket.socket().connect_ex((host, port)) != 0:
    #         print("Server is not active, exiting...")
    #         return False
            
    #     else:
    #         print("Server is active, continuing...")
    # except Exception as e:
    #     print(f"Error checking server connection: {str(e)}")
    #     return False
    
    # Check for a session id in the browser cookies
    session_id = request.COOKIES.get('session_id')
    # print('Session ID from cookies: ', session_id)
    # print('Session ID: ', session_id)
    if not session_id:
        try:
            url = f"{company_settings.api_url}/web/session/authenticate"

            payload = {
                "jsonrpc": "2.0",
                "params": {
                    "db": company_settings.database_name,
                    "login": company_settings.erp_username,
                    "password": company_settings.erp_password
                }
            }
            headers = {
                "User-Agent": "insomnia/11.5.0",
                "X-API-Key": api_key
            }

            # If we have an existing session_id, use it
            # if session_id:
            #     headers["cookie"] = f"session_id={session_id}"

            # Debug logging
            print("Making authentication request to:", url)
            print("Headers:", headers)
            print("Payload:", payload)

            response = requests.request("POST", url, json=payload, headers=headers)
        
            # Debug response
            print("Response status:", response.status_code)
            print("Response headers:", dict(response.headers))
            print("Response cookies:", dict(response.cookies))
            print("Response body:", response.text)
        
            if response.status_code == 200:
                result = response.json()
                if 'result' in result and result['result'] is not None:
                    # Get new session_id from cookies
                    new_session_id = response.cookies.get('session_id')
                    print('New Session ID: ', new_session_id)
                    # response.set_cookie('erp_session_id', new_session_id)
                    if new_session_id:
                        session_id = new_session_id
                    else:
                        # If no session_id in response but no error, try to get it from the result
                        session_id = result['result'].get('session_id')
                        
                    if session_id:
                        print(f"Successfully authenticated with session ID: {session_id}")
                    else:
                        print("Authentication succeeded but no valid session ID returned")
                        # Update weighing record with authentication error if weighing_record_id is provided
                        if weighing_record_id:
                            weighing_record = WeighingRecord.objects.get(id=weighing_record_id)
                            weighing_record.is_synced = False
                            weighing_record.last_sync_attempt = timezone.now()
                            weighing_record.sync_error_message = "Authentication succeeded but no valid session ID returned from ERP"
                            weighing_record.save()
                        return False
                elif 'error' in result:
                    error_message = result['error'].get('data', {}).get('message', 'Unknown authentication error')
                    print('Authentication error:', error_message)
                    # Update weighing record with authentication error if weighing_record_id is provided
                    if weighing_record_id:
                        weighing_record = WeighingRecord.objects.get(id=weighing_record_id)
                        weighing_record.is_synced = False
                        weighing_record.last_sync_attempt = timezone.now()
                        weighing_record.sync_error_message = f"ERP authentication failed: {error_message}"
                        weighing_record.save()
                    return False
                else:
                    print('Failed to authenticate - unexpected response format:', response.text)
                    # Update weighing record with authentication error if weighing_record_id is provided
                    if weighing_record_id:
                        weighing_record = WeighingRecord.objects.get(id=weighing_record_id)
                        weighing_record.is_synced = False
                        weighing_record.last_sync_attempt = timezone.now()
                        weighing_record.sync_error_message = f"ERP authentication failed: Unexpected response format - {response.text}"
                        weighing_record.save()
                    return False
            else:
                print('Failed to authenticate:', response.text)
                # Update weighing record with authentication error if weighing_record_id is provided
                if weighing_record_id:
                    weighing_record = WeighingRecord.objects.get(id=weighing_record_id)
                    weighing_record.is_synced = False
                    weighing_record.last_sync_attempt = timezone.now()
                    weighing_record.sync_error_message = f"ERP authentication failed with status {response.status_code}: {response.text}"
                    weighing_record.save()
                return False
                
        except requests.exceptions.ConnectionError as e:
            print(f"Connection error during ERP authentication: {str(e)}")
            # Update weighing record with connection error if weighing_record_id is provided
            if weighing_record_id:
                weighing_record = WeighingRecord.objects.get(id=weighing_record_id)
                weighing_record.is_synced = False
                weighing_record.last_sync_attempt = timezone.now()
                weighing_record.sync_error_message = f"ERP connection error during authentication: {str(e)}"
                weighing_record.save()
            return False
        except requests.exceptions.Timeout as e:
            print(f"Timeout error during ERP authentication: {str(e)}")
            # Update weighing record with timeout error if weighing_record_id is provided
            if weighing_record_id:
                weighing_record = WeighingRecord.objects.get(id=weighing_record_id)
                weighing_record.is_synced = False
                weighing_record.last_sync_attempt = timezone.now()
                weighing_record.sync_error_message = f"ERP timeout error during authentication: {str(e)}"
                weighing_record.save()
            return False
        except ValueError as e:  # JSON decode error
            print(f"Error parsing authentication response: {str(e)}")
            # Update weighing record with parsing error if weighing_record_id is provided
            if weighing_record_id:
                weighing_record = WeighingRecord.objects.get(id=weighing_record_id)
                weighing_record.is_synced = False
                weighing_record.last_sync_attempt = timezone.now()
                weighing_record.sync_error_message = f"Error parsing ERP authentication response: {str(e)}"
                weighing_record.save()
            return False
        except Exception as e:
            print(f"Error during ERP authentication: {str(e)}")
            # Update weighing record with general error if weighing_record_id is provided
            if weighing_record_id:
                weighing_record = WeighingRecord.objects.get(id=weighing_record_id)
                weighing_record.is_synced = False
                weighing_record.last_sync_attempt = timezone.now()
                weighing_record.sync_error_message = f"Error during ERP authentication: {str(e)}"
                weighing_record.save()
            return False
    
    
    if company_settings.erp_system.name == 'Odoo':
        # Check if allow_bale_insert is enabled for this process
        allow_bale_insert_enabled = False
        if process_id:
            try:
                process = WeighingProcess.objects.get(id=process_id)
                allow_bale_insert_enabled = process.allow_bale_insert
            except WeighingProcess.DoesNotExist:
                print(f"Process with id {process_id} not found")
        
        # If allow_bale_insert is enabled, call create-commercial-bale endpoint
        if allow_bale_insert_enabled:
            try:
                # Get the weighing record if weighing_record_id is provided
                weighing_record = None
                delivery_note_number = ''
                if weighing_record_id:
                    weighing_record = WeighingRecord.objects.get(id=weighing_record_id)
                    if weighing_record.delivery_note:
                        delivery_note_number = weighing_record.delivery_note.delivery_note_number
                elif delivery_note:  # Use the delivery_note parameter if available (for new records)
                    delivery_note_number = delivery_note.delivery_note_number
                
                # Get group, lot, and hessian numbers from custom data if they exist
                group_number = custom_data.get('group_number', '') if custom_data else ''
                lot_number = custom_data.get('lot_number', '') if custom_data else ''
                hessian_id = custom_data.get('hessian_id', '') if custom_data else ''
                
                # Prepare parameters for the API call
                params = {
                    'barcode': barcode,
                    'mass': f"{float(net_weight):.2f}",
                    'dnote_number': delivery_note_number,
                    'scale_id': scale_id,
                    'group_number': group_number or '0',
                    'lot_number': lot_number or '0',
                    'hessian_id': hessian_id or '0',
                    'location': 'A'  # Static location as requested
                }
                
                # Construct URL for create-commercial-bale endpoint
                url = f"{company_settings.api_url}/api/bales/create-commercial-bale"
                
                print(f"Calling create-commercial-bale with params: {params}")
                
                payload = ""
                
                # Make the API call
                headers = {
                    "User-Agent": "insomnia/11.5.0",
                    "X-API-Key": api_key
                }
                response = requests.request("POST", url, data=payload, headers=headers, params=params)
                
                print(f"create-commercial-bale response status: {response.status_code}")
                print(f"create-commercial-bale response text: {response.text}")
                
                if response.status_code in [200, 201]:  # 201 Created is also successful
                    # Check if the response body contains success=false
                    try:
                        response_json = response.json()
                        if isinstance(response_json, dict) and response_json.get('success') is False:
                            # The API returned 200 but with success=false in the body
                            error_message = response_json.get('message', response.text)
                            if weighing_record:
                                weighing_record.is_synced = False
                                weighing_record.last_sync_attempt = timezone.now()
                                weighing_record.sync_error_message = f"create-commercial-bale failed: {error_message}"
                                weighing_record.save()
                            return False
                        else:
                            # API call was successful (success is True or not present)
                            # Update weighing record with erp response if weighing_record exists
                            if weighing_record:
                                weighing_record.is_synced = True
                                weighing_record.last_sync_attempt = timezone.now()
                                weighing_record.save()
                            return True
                    except ValueError:
                        # If response is not JSON, assume success for 200/201 status
                        if weighing_record:
                            weighing_record.is_synced = True
                            weighing_record.last_sync_attempt = timezone.now()
                            weighing_record.save()
                        return True
                elif response.status_code >= 400:
                    # Handle client/server error responses
                    if weighing_record:
                        weighing_record.is_synced = False
                        weighing_record.last_sync_attempt = timezone.now()
                        error_message = response.text
                        # Try to extract a more user-friendly error message if available
                        try:
                            response_json = response.json()
                            if 'error' in response_json and 'data' in response_json['error']:
                                error_message = response_json['error']['data'].get('message', response.text)
                        except:
                            pass  # If we can't parse the JSON, use the raw response text
                        weighing_record.sync_error_message = f"create-commercial-bale failed with status {response.status_code}: {error_message}"
                        weighing_record.save()
                    return False
                else:
                    # Other status codes that aren't 200/201 but not 400+ errors
                    if weighing_record:
                        weighing_record.is_synced = False
                        weighing_record.last_sync_attempt = timezone.now()
                        weighing_record.sync_error_message = f"create-commercial-bale returned unexpected status {response.status_code}: {response.text}"
                        weighing_record.save()
                    return False
                    
            except requests.exceptions.ConnectionError as e:
                print(f"Connection error calling create-commercial-bale: {str(e)}")
                if weighing_record_id:
                    weighing_record = WeighingRecord.objects.get(id=weighing_record_id)
                    if weighing_record:
                        weighing_record.is_synced = False
                        weighing_record.last_sync_attempt = timezone.now()
                        weighing_record.sync_error_message = f"Connection error calling create-commercial-bale: {str(e)}"
                        weighing_record.save()
                return False
            except requests.exceptions.Timeout as e:
                print(f"Timeout error calling create-commercial-bale: {str(e)}")
                if weighing_record_id:
                    weighing_record = WeighingRecord.objects.get(id=weighing_record_id)
                    if weighing_record:
                        weighing_record.is_synced = False
                        weighing_record.last_sync_attempt = timezone.now()
                        weighing_record.sync_error_message = f"Timeout error calling create-commercial-bale: {str(e)}"
                        weighing_record.save()
                return False
            except Exception as e:
                print(f"Error calling create-commercial-bale: {str(e)}")
                if weighing_record_id:
                    weighing_record = WeighingRecord.objects.get(id=weighing_record_id)
                    if weighing_record:
                        weighing_record.is_synced = False
                        weighing_record.last_sync_attempt = timezone.now()
                        weighing_record.sync_error_message = f"Error calling create-commercial-bale: {str(e)}"
                        weighing_record.save()
                return False
        else:
            # Use existing logic for other cases
            # print("Sending to erp system")
            print(f"Sending barcode {barcode}, net weight {net_weight}, and scale id {scale_id} to erp system")
            # print('Session ID: ', session_id)
            # session_id = request.COOKIES.get('erp_session_id')
            # print('Session ID: ', session_id)
            if session_id:
                try:
                    base_url = ""
                    params = {}
                    if process_type in ['ctl_workflow', 'ctl_commercial_workflow']:
                        hessian_id = custom_data.get('hessian_id', '')
                        lot_number = custom_data.get('lot_number', '')
                        group_number = custom_data.get('group_number', '')
                        
                        base_url = f"{company_settings.api_url}/api/bales/update-mass/"
                        params = {
                            'barcode': barcode,
                            'mass': f"{float(net_weight):.2f}",
                            'scale_id': scale_id,
                            'hessian_id': hessian_id,
                            'lot_number': lot_number,
                            'group_number': group_number
                        }
                    else:
                        base_url = f"{company_settings.api_url}/receiving/scaleserver/manual_scale/{float(net_weight):.2f}/{barcode}"
                    
                    payload = ""
                    headers = {
                        "User-Agent": "insomnia/11.5.0",
                        "X-API-Key": api_key
                    }
                    response = requests.request("POST", base_url, data=payload, params=params, headers=headers)
                    print(f"Calling update-mass with params: {params}")
                    
                    print(response.status_code)
                    print(response.text)
                    
                    if response.status_code in [200, 201]:  # 201 Created is also successful
                        # Check if the response body contains success=false
                        try:
                            response_json = response.json()
                            if isinstance(response_json, dict) and response_json.get('success') is False:
                                # The API returned 200 but with success=false in the body
                                error_message = response_json.get('message', response.text)
                                if weighing_record_id:
                                    weighing_record = WeighingRecord.objects.get(id=weighing_record_id)
                                    weighing_record.is_synced = False
                                    weighing_record.last_sync_attempt = timezone.now()
                                    weighing_record.sync_error_message = f"ERP API call failed: {error_message}"
                                    weighing_record.save()
                                return False
                            else:
                                # API call was successful (success is True or not present)
                                # Update weighing record with erp response if weighing_record_id exists
                                if weighing_record_id:
                                    weighing_record = WeighingRecord.objects.get(id=weighing_record_id)
                                    weighing_record.is_synced = True
                                    weighing_record.last_sync_attempt = timezone.now()
                                    weighing_record.save()
                                return True
                        except ValueError:
                            # If response is not JSON, assume success for 200/201 status
                            if weighing_record_id:
                                weighing_record = WeighingRecord.objects.get(id=weighing_record_id)
                                weighing_record.is_synced = True
                                weighing_record.last_sync_attempt = timezone.now()
                                weighing_record.save()
                            return True
                    elif response.status_code >= 400:
                        # Handle client/server error responses
                        if weighing_record_id:
                            weighing_record = WeighingRecord.objects.get(id=weighing_record_id)
                            weighing_record.is_synced = False
                            weighing_record.last_sync_attempt = timezone.now()
                            error_message = response.text
                            # Try to extract a more user-friendly error message if available
                            try:
                                response_json = response.json()
                                if 'error' in response_json and 'data' in response_json['error']:
                                    error_message = response_json['error']['data'].get('message', response.text)
                            except:
                                pass  # If we can't parse the JSON, use the raw response text
                            weighing_record.sync_error_message = f"ERP API call failed with status {response.status_code}: {error_message}"
                            weighing_record.save()
                        return False
                    else:
                        # Other status codes that aren't 200/201 but not 400+ errors
                        if weighing_record_id:
                            weighing_record = WeighingRecord.objects.get(id=weighing_record_id)
                            weighing_record.is_synced = False
                            weighing_record.last_sync_attempt = timezone.now()
                            weighing_record.sync_error_message = f"ERP API call returned unexpected status {response.status_code}: {response.text}"
                            weighing_record.save()
                        return False
                except requests.exceptions.ConnectionError as e:
                    print(f"Connection error during ERP API call: {str(e)}")
                    if weighing_record_id:
                        weighing_record = WeighingRecord.objects.get(id=weighing_record_id)
                        weighing_record.is_synced = False
                        weighing_record.last_sync_attempt = timezone.now()
                        weighing_record.sync_error_message = f"Connection error during ERP API call: {str(e)}"
                        weighing_record.save()
                    return False
                except requests.exceptions.Timeout as e:
                    print(f"Timeout error during ERP API call: {str(e)}")
                    if weighing_record_id:
                        weighing_record = WeighingRecord.objects.get(id=weighing_record_id)
                        weighing_record.is_synced = False
                        weighing_record.last_sync_attempt = timezone.now()
                        weighing_record.sync_error_message = f"Timeout error during ERP API call: {str(e)}"
                        weighing_record.save()
                    return False
                except Exception as e:
                    print(f"Error sending to erp: {str(e)}")
                    if weighing_record_id:
                        weighing_record = WeighingRecord.objects.get(id=weighing_record_id)
                        weighing_record.is_synced = False
                        weighing_record.last_sync_attempt = timezone.now()
                        weighing_record.sync_error_message = str(e)
                        weighing_record.save()
                    return False
            else:
                print('No session id found')
                # Update weighing record with session error if weighing_record_id is provided
                if weighing_record_id:
                    weighing_record = WeighingRecord.objects.get(id=weighing_record_id)
                    weighing_record.is_synced = False
                    weighing_record.last_sync_attempt = timezone.now()
                    weighing_record.sync_error_message = "No valid session ID available for ERP communication"
                    weighing_record.save()
                return False
    else:
        # TODO: Add other erp systems here
        print('ONLY ODOO IS SUPPORTED FOR NOW')
        return False

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
            ser = serial.Serial(scale.com_port, 9600, timeout=2)
            if ser.is_open:
                # Clear any stale data from buffer
                for _ in range(3):
                    ser.reset_input_buffer()
                    time.sleep(0.05)
                # Send command to get weight
                ser.write(b"\r\n")
                # Read response
                time.sleep(0.3)
                line = ser.readline()
                # Decode bytes
                try:
                    decoded = line.decode('utf-8', errors='ignore').strip()
                except Exception:
                    decoded = line.decode(errors='ignore').strip()

                # Attempt to parse numeric weight from decoded string
                numeric_match = re.search(r'([-+]?\d+(?:[.,]\d+)?)\s*(kg|g|lbs|lb|pd)\b', decoded, re.IGNORECASE)

                if numeric_match:
                    num_str = numeric_match.group(1).replace(',', '')
                    unit = numeric_match.group(2).lower()
                    try:
                        weight = float(num_str)
                        return JsonResponse({
                            'success': True,
                            'weight': weight,
                            'unit': unit,
                            'scale_id': str(scale.scale_id) if scale.scale_id else None,
                            'scale_name': scale.name
                        })
                    except ValueError:
                        return JsonResponse({
                            'success': False,
                            'message': f'Could not parse numeric weight: {num_str}'
                        })
                else:
                    return JsonResponse({
                        'success': False,
                        'message': f'No numeric weight found in: {decoded}'
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
    
    

@login_required
@user_passes_test(is_admin)
def sync_all_unsynced(request):
    print('Syncing all unsynced weighing records')
    weighing_records = WeighingRecord.objects.filter(is_synced=False)
    for weighing_record in weighing_records:
        print('Syncing weighing record: ', weighing_record.id)
        success = send_to_erp(weighing_record.barcode, weighing_record.net_weight, weighing_record.weighing_scale_id, weighing_record.id, request, weighing_record.custom_data, weighing_record.process.process_type, weighing_record.process.id, weighing_record.delivery_note)
        
        if success and weighing_record.delivery_note:
            allow_spaces = False
            if weighing_record.process:
                allow_spaces = weighing_record.process.allow_spaces_in_barcode
            
            weighing_record.delivery_note.add_scanned_barcode(weighing_record.barcode, allow_spaces=allow_spaces)
            weighing_record.delivery_note.save()
    return redirect('scale:weighing_record_list')


#################################################################################################
# Product Management Views
@login_required
@user_passes_test(is_admin)
def product_list(request):
    products = Product.objects.all().order_by('name')
    return render(request, 'scale/product_list.html', {'products': products})

@login_required
@user_passes_test(is_admin)
def product_detail(request, pk):
    product = get_object_or_404(Product, pk=pk)
    return render(request, 'scale/product_detail.html', {'product': product})

@login_required
@user_passes_test(is_admin)
def product_create(request):
    if request.method == 'POST':
        form = ProductForm(request.POST)
        if form.is_valid():
            product = form.save()
            messages.success(request, f'Product {product.name} was created successfully.')
            return redirect('scale:product_list')
    else:
        form = ProductForm()
    
    return render(request, 'scale/product_create.html', {'form': form})

@login_required
@user_passes_test(is_admin)
def product_edit(request, pk):
    product = get_object_or_404(Product, pk=pk)
    
    if request.method == 'POST':
        form = ProductForm(request.POST, instance=product)
        if form.is_valid():
            product = form.save()
            messages.success(request, f'Product {product.name} was updated successfully.')
            return redirect('scale:product_detail', pk=product.pk)
    else:
        form = ProductForm(instance=product)
    
    return render(request, 'scale/product_edit.html', {'form': form, 'product': product})

@login_required
@user_passes_test(is_admin)
def product_delete(request, pk):
    product = get_object_or_404(Product, pk=pk)
    
    if request.method == 'POST':
        name = product.name
        product.delete()
        messages.success(request, f'Product {name} was deleted successfully.')
        return redirect('scale:product_list')
    
    # If not POST, redirect to detail page
    return redirect('scale:product_detail', pk=pk)



#################################################################################################
# Delivery Note Management Views
@login_required
@user_passes_test(is_admin)
def delivery_note_list(request):
    # Sort active dnotes (is_being_scanned=True) first, then by delivery_note_number
    delivery_notes = DeliveryNote.objects.all().order_by('-is_being_scanned', 'delivery_note_number')
    return render(request, 'scale/delivery_note_list.html', {'delivery_notes': delivery_notes})

@login_required
@user_passes_test(is_admin)
def delivery_note_detail(request, pk):
    delivery_note = get_object_or_404(DeliveryNote, pk=pk)
    weighing_records = WeighingRecord.objects.filter(delivery_note=delivery_note).order_by('-timestamp')
    return render(request, 'scale/delivery_note_detail.html', {
        'delivery_note': delivery_note,
        'weighing_records': weighing_records
    })

def generate_delivery_note_number():
    """Generate next delivery note number in format SDN-0001, SDN-0002, etc."""
    # Get the latest delivery note with SDN prefix
    latest_note = DeliveryNote.objects.filter(
        delivery_note_number__startswith='SDN-'
    ).order_by('delivery_note_number').last()
    
    if latest_note:
        # Extract the number part and increment
        try:
            number_part = latest_note.delivery_note_number.split('-')[1]
            next_number = int(number_part) + 1
        except (IndexError, ValueError):
            # If there's an issue parsing, start from 1
            next_number = 1
    else:
        # No existing delivery notes with SDN prefix
        next_number = 1
    
    return f"SDN-{next_number:04d}"

@login_required
@user_passes_test(is_admin)
def delivery_note_create(request):
    if request.method == 'POST':
        form = DeliveryNoteForm(request.POST)
        if form.is_valid():
            delivery_note = form.save(commit=False)
            delivery_note.created_by = request.user
            # Generate automatic delivery note number
            delivery_note.delivery_note_number = generate_delivery_note_number()
            delivery_note.save()
            messages.success(request, f'Delivery Note {delivery_note.delivery_note_number} was created successfully.')
            return redirect('scale:delivery_note_list')
    else:
        form = DeliveryNoteForm()
    
    return render(request, 'scale/delivery_note_create.html', {'form': form})

@login_required
@user_passes_test(is_admin)
def delivery_note_edit(request, pk):
    delivery_note = get_object_or_404(DeliveryNote, pk=pk)
    
    if request.method == 'POST':
        form = DeliveryNoteForm(request.POST, instance=delivery_note)
        if form.is_valid():
            delivery_note = form.save()
            messages.success(request, f'Delivery Note {delivery_note.delivery_note_number} was updated successfully.')
            return redirect('scale:delivery_note_detail', pk=delivery_note.pk)
    else:
        form = DeliveryNoteForm(instance=delivery_note)
    
    return render(request, 'scale/delivery_note_edit.html', {'form': form, 'delivery_note': delivery_note})

@login_required
@user_passes_test(is_admin)
def delivery_note_delete(request, pk):
    delivery_note = get_object_or_404(DeliveryNote, pk=pk)
    
    if request.method == 'POST':
        name = delivery_note.delivery_note_number
        delivery_note.delete()
        messages.success(request, f'Delivery Note {name} was deleted successfully.')
        return redirect('scale:delivery_note_list')
    
    # Show confirmation page on GET
    context = {
        'delivery_note': delivery_note,
    }
    return render(request, 'scale/delivery_note_delete.html', context)

# Display the bale recall page for a delivery note
@login_required
def delivery_note_bale_recall(request, pk):
    delivery_note = get_object_or_404(DeliveryNote, pk=pk)
    
    # Check if delivery note is being scanned
    if delivery_note.is_being_scanned:
        messages.error(request, "Cannot recall bales while delivery note is being scanned.")
        return redirect('scale:delivery_note_detail', pk=pk)
        
    context = {
        'delivery_note': delivery_note,
    }
    return render(request, 'scale/delivery_note_bale_recall.html', context)


@login_required
def recall_delivery_note(request, pk):
    delivery_note = get_object_or_404(DeliveryNote, pk=pk)
    
    # Only allow recall if Odoo state is 'laid'
    if not delivery_note.odoo_data or delivery_note.odoo_data.get('state') != 'laid':
        messages.error(request, "Delivery note can only be recalled when Odoo state is 'laid'.")
        return redirect('scale:delivery_note_detail', pk=pk)

    if request.method == 'POST':
        try:
            # Call external API
            company_settings = CompanySettings.objects.first()
            if not company_settings or not company_settings.api_url:
                messages.error(request, "Company API settings not configured.")
                return redirect('scale:delivery_note_detail', pk=pk)

            api_url = f"{company_settings.api_url}/api/bales/recall-all-bale"
            params = {'dnote_number': delivery_note.delivery_note_number}
            
            headers = {
                "User-Agent": "insomnia/11.5.0",
                "X-API-Key": company_settings.api_key
            }
            
            response = requests.post(api_url, params=params, headers=headers, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            
            if data.get('success'):
                # Delete local weighing records
                from django.db import transaction # Added this import for transaction.atomic
                with transaction.atomic():
                    WeighingRecord.objects.filter(delivery_note=delivery_note).delete()
                    # Reset scanned count and other local stats
                    delivery_note.scanned_barcodes = []
                    delivery_note.scanned_bales_count = 0
                    delivery_note.save()
                    
                messages.success(request, "Delivery note recalled successfully. Weighing records have been deleted.")
            else:
                error_msg = data.get('message', 'Unknown error from external API')
                messages.error(request, f"Failed to recall delivery note: {error_msg}")
                # Assuming logger is already imported, e.g., import logging; logger = logging.getLogger(__name__)
                logger.error(f"Failed to recall delivery note {delivery_note.delivery_note_number}: {error_msg}")
                
        except requests.RequestException as e:
            messages.error(request, f"Network error while recalling delivery note: {str(e)}")
            logger.error(f"Network error recalling delivery note {delivery_note.delivery_note_number}: {e}")
        except Exception as e:
            messages.error(request, f"An unexpected error occurred: {str(e)}")
            logger.error(f"Unexpected error recalling delivery note {delivery_note.delivery_note_number}: {e}")
            
    return redirect('scale:delivery_note_detail', pk=pk)

@login_required
@user_passes_test(is_admin)
def delivery_note_suspend(request, pk):
    """Suspend an active delivery note (remove from being scanned)"""
    if request.method == 'POST':
        delivery_note = get_object_or_404(DeliveryNote, pk=pk)
        
        if delivery_note.is_being_scanned:
            delivery_note.is_being_scanned = False
            delivery_note.save()
            
            return JsonResponse({
                'success': True,
                'message': f'Delivery note {delivery_note.delivery_note_number} suspended successfully.'
            })
        else:
            return JsonResponse({
                'success': False,
                'message': f'Delivery note {delivery_note.delivery_note_number} is not currently being scanned.'
            })
    
    return JsonResponse({'success': False, 'message': 'Invalid request method.'})

@login_required
@user_passes_test(is_admin)
def manual_sync_delivery_notes(request):
    """Manually trigger Odoo delivery note sync"""
    from django.core.cache import cache

    if request.method == 'POST':
        try:
            # Check if another sync is already running by checking the same lock
            lock_id = "sync_odoo_delivery_notes_lock"
            if cache.get(lock_id):
                # Another sync is already running
                return JsonResponse({
                    'success': False,
                    'message': 'Another sync is currently running, please wait for it to complete.'
                })

            # Call the sync task asynchronously
            task_result = sync_odoo_delivery_notes.delay()

            # Return success response
            return JsonResponse({
                'success': True,
                'message': 'Delivery note sync initiated successfully.',
                'task_id': str(task_result.id)  # Include task ID for potential tracking
            })
        except Exception as e:
            return JsonResponse({
                'success': False,
                'message': f'Error initiating sync: {str(e)}'
            })

    return JsonResponse({'success': False, 'message': 'Invalid request method.'})


@login_required
@user_passes_test(is_admin)
def close_delivery_note(request, pk):
    """Explicitly close a delivery note after user confirmation."""
    if request.method == 'POST':
        delivery_note = get_object_or_404(DeliveryNote, pk=pk)

        # Only close if it's currently being scanned (i.e., ready for closure)
        if delivery_note.is_being_scanned:
            # Check if the delivery note is actually scanning complete before updating Odoo status
            if not delivery_note.is_scanning_complete():
                return JsonResponse({
                    'success': False,
                    'message': f'Delivery note {delivery_note.delivery_note_number} is not fully scanned. Cannot update Odoo status to laid.'
                })

            # Check for unsynced records
            unsynced_count = WeighingRecord.objects.filter(delivery_note=delivery_note, is_synced=False).count()
            if unsynced_count > 0:
                return JsonResponse({
                    'success': False,
                    'message': f'Cannot close delivery note. There are {unsynced_count} unsynced weighing records. Please resolve these errors first.'
                })

            # First, update the status in Odoo to 'laid'
            success = update_dnote_completion_status_with_api_key(delivery_note)
            if not success:
                return JsonResponse({
                    'success': False,
                    'message': f'Failed to update Odoo status for delivery note {delivery_note.delivery_note_number}. Delivery note was not closed locally.'
                })

            # Update local status
            delivery_note.is_being_scanned = False
            delivery_note.status = 'Closed' # Assuming 'Closed' is a valid status
            delivery_note.save()

            return JsonResponse({
                'success': True,
                'message': f'Delivery note {delivery_note.delivery_note_number} has been successfully closed and status updated in Odoo.'
            })
        else:
            return JsonResponse({
                'success': False,
                'message': f'Delivery note {delivery_note.delivery_note_number} is not in a state to be closed.'
            })

    return JsonResponse({'success': False, 'message': 'Invalid request method.'})


def update_dnote_completion_status_with_api_key(delivery_note):
    """Send a request to Odoo to update the delivery note status to laid, using API key"""
    try:
        # Get company settings for API URL and API key
        company_settings = CompanySettings.objects.first()
        if not company_settings or not company_settings.api_url:
            logger.error("No company settings found or API URL not configured")
            return False

        # Get API key from company settings
        api_key = company_settings.api_key if company_settings and company_settings.api_key else None
        if not api_key:
            logger.error("API key not configured in company settings")
            return False

        url = f"{company_settings.api_url}/api/grower-delivery-notes/update-status"

        querystring = {
            "document_number": delivery_note.delivery_note_number,
            "status": "laid"
        }

        payload = ""

        headers = {
            "User-Agent": "insomnia/11.5.0",
            "X-API-Key": api_key
        }

        response = requests.request("POST", url, data=payload, headers=headers, params=querystring)

        if response.status_code in [200, 201]:
            # Check if the response body contains success=false
            try:
                response_json = response.json()
                if isinstance(response_json, dict) and response_json.get('success') is False:
                    # The API returned 200 but with success=false in the body
                    error_message = response_json.get('message', response.text)
                    logger.error(f"Odoo API returned success=false for delivery note {delivery_note.delivery_note_number}. Error: {error_message}")
                    return False
                else:
                    # API call was successful (success is True or not present)
                    logger.info(f"Successfully updated Odoo status to 'laid' for delivery note {delivery_note.delivery_note_number}")
                    return True
            except ValueError:  # JSON decode error
                # If response is not JSON, assume success for 200/201 status
                logger.info(f"Successfully updated Odoo status to 'laid' for delivery note {delivery_note.delivery_note_number} (non-JSON response)")
                return True
        elif response.status_code >= 400:
            # Handle client/server error responses
            error_message = response.text
            # Try to extract a more user-friendly error message if available
            try:
                response_json = response.json()
                if 'error' in response_json and 'data' in response_json['error']:
                    error_message = response_json['error']['data'].get('message', response.text)
            except:
                pass  # If we can't parse the JSON, use the raw response text
            logger.error(f"Failed to update Odoo status. Status: {response.status_code}. Error: {error_message}")
            return False
        else:
            # Other status codes that aren't 200/201 but not 400+ errors
            logger.error(f"Unexpected response from Odoo. Status: {response.status_code}. Response: {response.text}")
            return False

    except requests.exceptions.ConnectionError as e:
        logger.error(f"Connection error when updating Odoo status for {delivery_note.delivery_note_number}: {str(e)}")
        return False
    except requests.exceptions.Timeout as e:
        logger.error(f"Timeout error when updating Odoo status for {delivery_note.delivery_note_number}: {str(e)}")
        return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Request error when updating Odoo status for {delivery_note.delivery_note_number}: {str(e)}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error updating Odoo status for {delivery_note.delivery_note_number}: {str(e)}")
        return False


@user_passes_test(is_admin)
def deactivate_active_delivery_note(request, pk):
    """Deactivate an active delivery note without necessarily closing it completely."""
    if request.method == 'POST':
        delivery_note = get_object_or_404(DeliveryNote, pk=pk)
        
        # Deactivate the scanning state but preserve the status if it's not complete
        if delivery_note.is_being_scanned:
            delivery_note.is_being_scanned = False
            # Only change to closed if it was actually completed
            if delivery_note.is_scanning_complete():
                # Check for unsynced records before closing
                unsynced_count = WeighingRecord.objects.filter(delivery_note=delivery_note, is_synced=False).count()
                if unsynced_count == 0:
                    delivery_note.status = 'Closed'
                # If there are unsynced records, we leave it as 'Open' (or whatever it was) 
                # but still allow deactivation of scanning state.
            
            delivery_note.save()
            
            return JsonResponse({
                'success': True,
                'message': f'Delivery note {delivery_note.delivery_note_number} has been deactivated and is no longer active for scanning.'
            })
        else:
            return JsonResponse({
                'success': False,
                'message': f'Delivery note {delivery_note.delivery_note_number} is not currently active for scanning.'
            })
    
    return JsonResponse({'success': False, 'message': 'Invalid request method.'})


@login_required
@user_passes_test(is_admin)
def delivery_note_bale_recall(request, pk):
    """Display the bale recall page for a delivery note"""
    delivery_note = get_object_or_404(DeliveryNote, pk=pk)
    
    # Get all bales from odoo_data
    bales = []
    if delivery_note.odoo_data and 'bales' in delivery_note.odoo_data:
        bales = delivery_note.odoo_data['bales']
    
    # Mark which bales have been scanned
    scanned_barcodes = set(delivery_note.scanned_barcodes)
    for bale in bales:
        bale['is_scanned'] = bale.get('scale_barcode', '').strip() in scanned_barcodes
    
    context = {
        'delivery_note': delivery_note,
        'bales': bales,
    }
    return render(request, 'scale/delivery_note_bale_recall.html', context)

@login_required
@user_passes_test(is_admin)
def recall_bale(request, pk):
    """Handle individual bale recall requests"""
    if request.method == 'POST':
        delivery_note = get_object_or_404(DeliveryNote, pk=pk)
        barcode = request.POST.get('barcode', '').strip()
        
        if not barcode:
            return JsonResponse({
                'success': False,
                'message': 'Barcode is required.'
            })
        
        # Check if barcode exists in delivery note's bales
        bale_found = False
        if delivery_note.odoo_data and 'bales' in delivery_note.odoo_data:
            for bale in delivery_note.odoo_data['bales']:
                if bale.get('scale_barcode', '').strip() == barcode:
                    bale_found = True
                    break
        
        if not bale_found:
            return JsonResponse({
                'success': False,
                'message': f'Barcode {barcode} not found in delivery note {delivery_note.delivery_note_number}.'
            })
        
        # Check if barcode has been scanned
        if barcode not in delivery_note.scanned_barcodes:
            return JsonResponse({
                'success': False,
                'message': f'Barcode {barcode} has not been scanned yet.'
            })
        
        try:
            # Send request to Odoo to update bale mass to 0
            company_settings = CompanySettings.objects.first()
            if not company_settings or not company_settings.api_url:
                return JsonResponse({
                    'success': False,
                    'message': 'Company API settings not configured.'
                })
            
            # Get API key from company settings
            api_key = company_settings.api_key if company_settings and company_settings.api_key else None
            
            # Try to get session ID from cookies or attempt authentication if needed
            session_id = request.COOKIES.get('session_id')
            
            # Attempt authentication if no session_id exists
            if not session_id:
                auth_url = f"{company_settings.api_url}/web/session/authenticate"
                auth_payload = {
                    "jsonrpc": "2.0",
                    "params": {
                        "db": company_settings.database_name,
                        "login": company_settings.erp_username,
                        "password": company_settings.erp_password
                    }
                }
                auth_headers = {
                    "User-Agent": "insomnia/11.5.0",
                    "X-API-Key": api_key
                }
                
                print("Making authentication request to:", auth_url)
                auth_response = requests.post(auth_url, json=auth_payload, headers=auth_headers, timeout=10)
                
                if auth_response.status_code == 200:
                    auth_result = auth_response.json()
                    if 'result' in auth_result and auth_result['result'] is not None:
                        # Get session ID from response cookies
                        new_session_id = auth_response.cookies.get('session_id')
                        if new_session_id:
                            session_id = new_session_id
                        else:
                            # If no session_id in cookies, try to get from result
                            session_id = auth_result['result'].get('session_id')
                            
                        if session_id:
                            print(f"Successfully authenticated with session ID: {session_id}")
                        else:
                            return JsonResponse({
                                'success': False,
                                'message': 'Authentication succeeded but no valid session ID returned from ERP'
                            })
                    elif 'error' in auth_result:
                        error_message = auth_result['error'].get('data', {}).get('message', 'Unknown authentication error')
                        print('Authentication error:', error_message)
                        return JsonResponse({
                            'success': False,
                            'message': f'ERP authentication failed: {error_message}'
                        })
                    else:
                        print('Failed to authenticate - unexpected response format:', auth_response.text)
                        return JsonResponse({
                            'success': False,
                            'message': f'ERP authentication failed: Unexpected response format'
                        })
                else:
                    print('Failed to authenticate:', auth_response.text)
                    return JsonResponse({
                        'success': False,
                        'message': f'ERP authentication failed with status {auth_response.status_code}: {auth_response.text}'
                    })
            
            # Get the existing weighing record to retrieve hessian value
            weighing_record = WeighingRecord.objects.filter(
                delivery_note=delivery_note,
                barcode=barcode
            ).first()
            
            # Build the API URL with hessian if available
            api_url = f"{company_settings.api_url}/api/bales/recall-bale/?barcode={barcode}"
            
            headers = {
                "User-Agent": "insomnia/11.5.0",
                "X-API-Key": api_key
            }
            
            response = requests.post(api_url, headers=headers, timeout=10)
            
            # Check response status codes
            if response.status_code in [200, 201]:  # Success codes
                # Success - update local database
                # Remove barcode from scanned_barcodes
                delivery_note.scanned_barcodes = [b for b in delivery_note.scanned_barcodes if b != barcode]
                
                # Decrement scanned_bales_count
                if delivery_note.scanned_bales_count > 0:
                    delivery_note.scanned_bales_count -= 1
                
                delivery_note.save()
                
                # Delete the weighing record
                WeighingRecord.objects.filter(
                    delivery_note=delivery_note,
                    barcode=barcode
                ).delete()
                
                return JsonResponse({
                    'success': True,
                    'message': f'Bale {barcode} has been successfully recalled.',
                    'scanned_count': delivery_note.scanned_bales_count,
                    'total_count': delivery_note.get_bale_count()
                })
            elif response.status_code >= 400:
                # Handle client/server error responses
                error_message = response.text
                # Try to extract a more user-friendly error message if available
                try:
                    response_json = response.json()
                    if 'error' in response_json and 'data' in response_json['error']:
                        error_message = response_json['error']['data'].get('message', response.text)
                except:
                    pass  # If we can't parse the JSON, use the raw response text
                return JsonResponse({
                    'success': False,
                    'message': f'Failed to update bale in Odoo. Status: {response.status_code}. Error: {error_message}'
                })
            else:
                # Other status codes that aren't 200/201 but not 400+ errors
                return JsonResponse({
                    'success': False,
                    'message': f'Unexpected response from Odoo. Status: {response.status_code}. Response: {response.text}'
                })
                
        except requests.exceptions.ConnectionError as e:
            print(f"Connection error during recall_bale: {str(e)}")
            return JsonResponse({
                'success': False,
                'message': f'Connection error communicating with Odoo: {str(e)}'
            })
        except requests.exceptions.Timeout as e:
            print(f"Timeout error during recall_bale: {str(e)}")
            return JsonResponse({
                'success': False,
                'message': f'Timeout error communicating with Odoo: {str(e)}'
            })
        except requests.RequestException as e:
            print(f"Request error during recall_bale: {str(e)}")
            return JsonResponse({
                'success': False,
                'message': f'Error communicating with Odoo: {str(e)}'
            })
        except Exception as e:
            print(f"Unexpected error in recall_bale: {str(e)}")
            return JsonResponse({
                'success': False,
                'message': f'Unexpected error: {str(e)}'
            })
    
    return JsonResponse({
        'success': False,
        'message': 'Invalid request method.'
    })


@login_required
@user_passes_test(is_admin)
def close_commercial_delivery_note(request, pk):
    """
    Close a commercial delivery note via external API.
    This is used for the ctl_commercial_workflow.
    """
    if request.method != 'POST':
        return JsonResponse({
            'success': False,
            'message': 'Invalid request method.'
        })
    
    delivery_note = get_object_or_404(DeliveryNote, pk=pk)
    
    # Get company settings for API configuration
    company_settings = CompanySettings.objects.first()
    if not company_settings or not company_settings.api_url:
        return JsonResponse({
            'success': False,
            'message': 'Company settings or API URL not configured.'
        })
        
    api_key = company_settings.api_key
    
    try:
        # Construct the API URL
        # The user specified: company_settings.api_url/api/grower-delivery-notes/close-marshalled-at-scale
        # And it takes document_number as a parameter
        url = f"{company_settings.api_url}/api/grower-delivery-notes/close-marshalled-at-scale"
        
        # Prepare parameters
        params = {
            'document_number': delivery_note.delivery_note_number
        }
        
        headers = {
            "User-Agent": "insomnia/11.5.0",
            "X-API-Key": api_key
        }
        
        print(f"Calling close-marshalled-at-scale with params: {params}")
        
        # Make the API call
        # User confirmed POST request
        response = requests.post(url, params=params, headers=headers, timeout=10)
        
        print(f"close-marshalled-at-scale response status: {response.status_code}")
        print(f"close-marshalled-at-scale response text: {response.text}")
        
        if response.status_code in [200, 201]:
            try:
                response_json = response.json()
                
                # Check for success flag in response body if present
                if isinstance(response_json, dict):
                    if response_json.get('success') is False:
                        return JsonResponse({
                            'success': False,
                            'message': response_json.get('message', 'External API returned failure.')
                        })
                
                # If successful, we should also close/deactivate the delivery note locally
                # similar to close_delivery_note view but without the local validation checks
                # since the external API handles that logic
                
                delivery_note.status = 'Closed'
                delivery_note.is_being_scanned = False
                delivery_note.save()
                
                return JsonResponse({
                    'success': True,
                    'message': f'Delivery note {delivery_note.delivery_note_number} closed successfully.'
                })
                
            except ValueError:
                # If response is not JSON but status is 200, assume success
                delivery_note.status = 'Closed'
                delivery_note.is_being_scanned = False
                delivery_note.save()
                
                return JsonResponse({
                    'success': True,
                    'message': f'Delivery note {delivery_note.delivery_note_number} closed successfully.'
                })
        else:
            # Handle error status codes
            error_message = f"API Error {response.status_code}: {response.text}"
            try:
                response_json = response.json()
                if isinstance(response_json, dict) and 'message' in response_json:
                    error_message = response_json['message']
            except:
                pass
                
            return JsonResponse({
                'success': False,
                'message': error_message
            })
            
    except requests.Timeout as e:
        print(f"Timeout error during close_commercial_delivery_note: {str(e)}")
        return JsonResponse({
            'success': False,
            'message': f'Timeout error communicating with external API: {str(e)}'
        })
    except requests.RequestException as e:
        print(f"Request error during close_commercial_delivery_note: {str(e)}")
        return JsonResponse({
            'success': False,
            'message': f'Error communicating with external API: {str(e)}'
        })
    except Exception as e:
        print(f"Unexpected error in close_commercial_delivery_note: {str(e)}")
        return JsonResponse({
            'success': False,
            'message': f'Unexpected error: {str(e)}'
        })

#################################################################################################
# Weighing Record Management Views
@login_required
@user_passes_test(is_admin)
def weighing_record_list(request):
    # Get filter parameters from request
    scale_id = request.GET.get('scale')
    product_id = request.GET.get('product')
    process_id = request.GET.get('process')
    barcode = request.GET.get('barcode')
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    sort_param = request.GET.get('sort', '-timestamp')  # Default sort by timestamp desc
    
    # Start with all records
    records = WeighingRecord.objects.all()
    
    # Apply filters if provided
    if scale_id:
        records = records.filter(scale__scale_id=scale_id)
    
    if product_id:
        records = records.filter(product_id=product_id)
    
    if process_id:
        records = records.filter(process_id=process_id)
    
    if barcode:
        records = records.filter(barcode__icontains=barcode)
    
    # Date range filtering
    from django.utils.dateparse import parse_date
    from datetime import datetime, timedelta, time
    
    if date_from:
        date_from_obj = parse_date(date_from)
        if date_from_obj:
            date_from_datetime = datetime.combine(date_from_obj, time.min)
            records = records.filter(timestamp__gte=date_from_datetime)
    
    if date_to:
        date_to_obj = parse_date(date_to)
        if date_to_obj:
            date_to_datetime = datetime.combine(date_to_obj, time.max)
            records = records.filter(timestamp__lte=date_to_datetime)
    
    # Apply sorting
    if sort_param:
        records = records.order_by(sort_param)
    
    # Pagination
    paginator = Paginator(records, 10)  # Show 10 records per page
    page_number = request.GET.get('page', 1)
    records_page = paginator.get_page(page_number)
    
    # Get lists for filter dropdowns
    scales = Scale.objects.all().order_by('name')
    products = Product.objects.all().order_by('name')
    processes = WeighingProcess.objects.all().order_by('name')
    
    context = {
        'records': records_page,
        'scales': scales,
        'products': products,
        'processes': processes,
    }
    
    return render(request, 'scale/weighing_record_list.html', context)

@login_required
@user_passes_test(is_admin)
def weighing_record_detail(request, pk):
    record = get_object_or_404(WeighingRecord, pk=pk)
    return render(request, 'scale/weighing_record_detail.html', {'record': record})

@login_required
@user_passes_test(is_admin)
def print_weighing_record(request, pk):
    record = get_object_or_404(WeighingRecord, pk=pk)
    
    # Update print count
    record.print_count += 1
    record.last_printed_at = timezone.now()
    record.save()
    
    # For now, just redirect back to the detail page with a success message
    messages.success(request, f'Weighing record {pk} sent to printer.')
    return redirect('scale:weighing_record_detail', pk=record.pk)

@login_required
@user_passes_test(is_admin)
def weighing_record_edit(request, pk):
    record = get_object_or_404(WeighingRecord, pk=pk)
    
    if request.method == 'POST':
        # For now, handle basic fields manually since we don't have a form
        try:
            record.gross_weight = Decimal(request.POST.get('gross_weight', record.gross_weight))
            record.tare_weight = Decimal(request.POST.get('tare_weight', record.tare_weight))
            record.net_weight = Decimal(request.POST.get('net_weight', record.net_weight))
            record.unit_of_measure = request.POST.get('unit_of_measure', record.unit_of_measure)
            record.notes = request.POST.get('notes', record.notes)
            record.barcode = request.POST.get('barcode', record.barcode)
            
            # Handle delivery note association
            delivery_note_id = request.POST.get('delivery_note_id')
            if delivery_note_id:
                record.delivery_note = get_object_or_404(DeliveryNote, pk=delivery_note_id)
            elif 'remove_delivery_note' in request.POST:
                record.delivery_note = None
            
            record.save()
            messages.success(request, 'Weighing record updated successfully.')
            return redirect('scale:weighing_record_detail', pk=record.pk)
        except Exception as e:
            messages.error(request, f'Error updating record: {str(e)}')
    
    # For edit, we should create a form, but for now, just render a template with the record
    context = {
        'record': record,
        'delivery_notes': DeliveryNote.objects.all().order_by('-created_at'),
        'scales': Scale.objects.filter(is_active=True),
        'products': Product.objects.filter(is_active=True),
        'processes': WeighingProcess.objects.filter(is_active=True),
    }
    
    return render(request, 'scale/weighing_record_edit.html', context)

@login_required
@user_passes_test(is_admin)
def weighing_record_delete(request, pk):
    record = get_object_or_404(WeighingRecord, pk=pk)
    
    if request.method == 'POST':
        record_id = record.id
        record.delete()
        messages.success(request, f'Weighing record #{record_id} deleted successfully.')
        return redirect('scale:weighing_record_list')
    
    # If not POST, redirect to detail page
    return redirect('scale:weighing_record_detail', pk=pk)


#################################################################################################
# Export Weighing Records
@login_required
@user_passes_test(is_admin)
def export_weighing_records(request):
    # Get filter parameters from request (same as in weighing_record_list)
    scale_id = request.GET.get('scale')
    product_id = request.GET.get('product')
    process_id = request.GET.get('process')
    barcode = request.GET.get('barcode')
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    sort_param = request.GET.get('sort', '-timestamp')
    export_format = request.GET.get('format', 'csv')
    
    # Start with all records
    records = WeighingRecord.objects.all()
    
    # Apply filters if provided
    if scale_id:
        records = records.filter(scale_id=scale_id)
    
    if product_id:
        records = records.filter(product_id=product_id)
    
    if process_id:
        records = records.filter(process_id=process_id)
    
    if barcode:
        records = records.filter(barcode__icontains=barcode)
    
    # Date range filtering
    from django.utils.dateparse import parse_date
    from datetime import datetime, timedelta, time
    
    if date_from:
        date_from_obj = parse_date(date_from)
        if date_from_obj:
            date_from_datetime = datetime.combine(date_from_obj, time.min)
            records = records.filter(timestamp__gte=date_from_datetime)
    
    if date_to:
        date_to_obj = parse_date(date_to)
        if date_to_obj:
            date_to_datetime = datetime.combine(date_to_obj, time.max)
            records = records.filter(timestamp__lte=date_to_datetime)
    
    # Apply sorting
    if sort_param:
        records = records.order_by(sort_param)
    
    # Process export based on format
    if export_format == 'csv':
        return export_records_to_csv(records)
    elif export_format == 'excel':
        return export_records_to_excel(records)
    elif export_format == 'pdf':
        return export_records_to_pdf(records)
    else:
        messages.error(request, f"Unsupported export format: {export_format}")
        return redirect('scale:weighing_record_list')

def export_records_to_csv(records):
    import csv
    from django.http import HttpResponse
    
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="weighing_records.csv"'
    
    writer = csv.writer(response)
    writer.writerow(['ID', 'Timestamp', 'Barcode', 'Scale', 'Product', 'Process', 'Gross Weight', 
                    'Tare Weight', 'Net Weight', 'Unit', 'Recorded By', 'Notes', 'Delivery Note'])
    
    for record in records:
        writer.writerow([
            record.id,
            record.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            record.barcode,
            record.scale.name,
            record.product.name,
            record.process.name,
            record.gross_weight,
            record.tare_weight,
            record.net_weight,
            record.unit_of_measure,
            f"{record.user.first_name} {record.user.last_name}",
            record.notes,
            record.delivery_note.delivery_note_number if record.delivery_note else ''
        ])
    
    return response

def export_records_to_excel(records):
    import xlwt
    from django.http import HttpResponse
    from datetime import datetime
    
    response = HttpResponse(content_type='application/ms-excel')
    response['Content-Disposition'] = 'attachment; filename="weighing_records.xls"'
    
    wb = xlwt.Workbook(encoding='utf-8')
    ws = wb.add_sheet('Weighing Records')
    
    # Sheet header, first row
    row_num = 0
    
    font_style = xlwt.XFStyle()
    font_style.font.bold = True
    
    columns = ['ID', 'Timestamp', 'Barcode', 'Scale', 'Product', 'Process', 'Gross Weight', 
              'Tare Weight', 'Net Weight', 'Unit', 'Recorded By', 'Notes', 'Delivery Note']
    
    for col_num in range(len(columns)):
        ws.write(row_num, col_num, columns[col_num], font_style)
    
    # Sheet body, remaining rows
    font_style = xlwt.XFStyle()
    
    for record in records:
        row_num += 1
        ws.write(row_num, 0, record.id, font_style)
        ws.write(row_num, 1, record.timestamp.strftime('%Y-%m-%d %H:%M:%S'), font_style)
        ws.write(row_num, 2, record.barcode, font_style)
        ws.write(row_num, 3, record.scale.name, font_style)
        ws.write(row_num, 4, record.product.name, font_style)
        ws.write(row_num, 5, record.process.name, font_style)
        ws.write(row_num, 6, float(record.gross_weight), font_style)
        ws.write(row_num, 7, float(record.tare_weight), font_style)
        ws.write(row_num, 8, float(record.net_weight), font_style)
        ws.write(row_num, 9, record.unit_of_measure, font_style)
        ws.write(row_num, 10, f"{record.user.first_name} {record.user.last_name}", font_style)
        ws.write(row_num, 11, record.notes, font_style)
        ws.write(row_num, 12, record.delivery_note.delivery_note_number if record.delivery_note else '', font_style)
    
    wb.save(response)
    return response

def export_records_to_pdf(records):
    from django.http import HttpResponse
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter, landscape
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
    from reportlab.lib.styles import getSampleStyleSheet
    from io import BytesIO
    
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(letter))
    elements = []
    
    # Add title
    styles = getSampleStyleSheet()
    elements.append(Paragraph("Weighing Records", styles['Title']))
    elements.append(Paragraph(f"Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", styles['Normal']))
    
    # Table data
    data = [['ID', 'Timestamp', 'Barcode', 'Scale', 'Product', 'Process', 'Gross', 
            'Tare', 'Net', 'Unit', 'Recorded By', 'Delivery Note']]
    
    for record in records:
        data.append([
            str(record.id),
            record.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
            record.barcode,
            record.scale.name,
            record.product.name,
            record.process.name,
            str(record.gross_weight),
            str(record.tare_weight),
            str(record.net_weight),
            record.unit_of_measure,
            f"{record.user.first_name} {record.user.last_name}",
            record.delivery_note.delivery_note_number if record.delivery_note else ''
        ])
    
    # Create the table
    table = Table(data)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 10),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.white),
        ('TEXTCOLOR', (0, 1), (-1, -1), colors.black),
        ('ALIGN', (0, 1), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 1, colors.black),
    ]))
    
    # Add the table to elements
    elements.append(table)
    
    # Build the PDF
    doc.build(elements)
    
    # Get the value of the buffer
    pdf = buffer.getvalue()
    buffer.close()
    
    # Create the HTTP response
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="weighing_records.pdf"'
    response.write(pdf)
    
    return response

###################################################################################################
#  Company Settings
@login_required
@user_passes_test(is_admin)
def company_settings(request):
    # Get the first company settings record or None
    company_settings = CompanySettings.objects.first()
    
    if request.method == 'POST':
        form = CompanySettingsForm(request.POST, instance=company_settings)
        if form.is_valid():
            form.save()
            messages.success(request, 'Company settings saved successfully.')
            return redirect('scale:company_settings')
    else:
        form = CompanySettingsForm(instance=company_settings)
    
    return render(request, 'scale/company_settings.html', {
        'form': form,
        'company_settings': company_settings
    })

@login_required
@user_passes_test(is_admin)
def print_delivery_note(request, pk):
    """Generate and return a professionally styled PDF for the delivery note"""
    delivery_note = get_object_or_404(DeliveryNote, pk=pk)
    
    from django.http import HttpResponse
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter, A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, PageBreak
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch, mm
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from io import BytesIO
    import os
    
    # Create QR code if it doesn't exist
    if not delivery_note.qr_code:
        delivery_note.generate_qr_code()
        delivery_note.save()
    
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, 
        pagesize=A4,
        topMargin=0.75*inch,
        bottomMargin=0.75*inch,
        leftMargin=0.75*inch,
        rightMargin=0.75*inch
    )
    elements = []
    
    # Define custom styles
    styles = getSampleStyleSheet()
    
    # Company header style
    company_style = ParagraphStyle(
        'CompanyHeader',
        parent=styles['Normal'],
        fontSize=24,
        fontName='Helvetica-Bold',
        spaceAfter=5,
        alignment=TA_LEFT,
        textColor=colors.HexColor('#1f2937')
    )
    
    # Title style
    title_style = ParagraphStyle(
        'DocumentTitle',
        parent=styles['Normal'],
        fontSize=20,
        fontName='Helvetica-Bold',
        spaceAfter=30,
        spaceBefore=10,
        alignment=TA_CENTER,
        textColor=colors.HexColor('#374151'),
        # borderWidth=2,
        # borderColor=colors.HexColor('#6b7280'),
        # borderPadding=10,
        # backColor=colors.HexColor('#f9fafb')
    )
    
    # Section header style
    section_style = ParagraphStyle(
        'SectionHeader',
        parent=styles['Normal'],
        fontSize=14,
        fontName='Helvetica-Bold',
        spaceAfter=10,
        spaceBefore=20,
        textColor=colors.HexColor('#374151'),
        # borderWidth=1,
        # borderColor=colors.HexColor('#d1d5db'),
        # leftIndent=0,
        # borderPadding=8,
        # backColor=colors.HexColor('#f3f4f6')
    )
    
    # Info text style
    info_style = ParagraphStyle(
        'InfoText',
        parent=styles['Normal'],
        fontSize=10,
        fontName='Helvetica',
        textColor=colors.HexColor('#4b5563')
    )
    
    # Header with company name and QR code
    header_data = []
    
    # Create QR code image for PDF from media folder
    qr_img = None
    if delivery_note.qr_code and delivery_note.qr_code.name:
        try:
            # Get the full path to the QR code file in media folder
            from django.conf import settings
            qr_code_path = os.path.join(settings.MEDIA_ROOT, delivery_note.qr_code.name)
            
            # Check if file exists and create reportlab Image
            if os.path.exists(qr_code_path):
                qr_img = Image(qr_code_path, width=1.2*inch, height=1.2*inch)
        except Exception as e:
            # If QR code fails, continue without it
            pass
    
    # Company header with QR code
    if qr_img:
        header_table_data = [
            [Paragraph("IntelliScale", company_style), qr_img],
            [Paragraph("Weighing Management System", info_style), Paragraph(f"QR: {delivery_note.delivery_note_number}", info_style)]
        ]
        header_table = Table(header_table_data, colWidths=[5*inch, 1.5*inch])
        header_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'CENTER'),
            ('TOPPADDING', (0, 0), (-1, -1), 10),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
        ]))
        elements.append(header_table)
    else:
        elements.append(Paragraph("IntelliScale", company_style))
        elements.append(Paragraph("Weighing Management System", info_style))
    
    elements.append(Spacer(1, 20))
    
    # Document title
    elements.append(Paragraph("DELIVERY NOTE", title_style))
    elements.append(Spacer(1, 10))
    
    # Basic information section
    elements.append(Paragraph("Delivery Information", section_style))
    
    info_data = [
        ['Delivery Note Number:', delivery_note.delivery_note_number or 'N/A'],
        # ['Status:', delivery_note.status or 'N/A'],
        ['Created By:', f"{delivery_note.created_by.first_name} {delivery_note.created_by.last_name}"],
        ['Created Date:', delivery_note.created_at.strftime('%B %d, %Y at %I:%M %p')],
        # ['Last Updated:', delivery_note.updated_at.strftime('%B %d, %Y at %I:%M %p')],
        # ['Sync Status:', 'Synced ✅' if delivery_note.is_synced else 'Not Synced ⏳'],
    ]
    
    info_table = Table(info_data, colWidths=[2.2*inch, 4*inch])
    info_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
        ('ALIGN', (1, 0), (1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e5e7eb')),
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#fafafa')),
        ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#374151')),
        ('TEXTCOLOR', (1, 0), (1, -1), colors.HexColor('#111827')),
    ]))
    
    elements.append(info_table)
    
    # Vehicle and driver information section
    elements.append(Paragraph("Vehicle & Driver Information", section_style))
    
    vehicle_data = [
        ['Driver:', delivery_note.driver.name if delivery_note.driver else 'Not assigned'],
        ['Phone:', delivery_note.driver.phone if delivery_note.driver and delivery_note.driver.phone else 'N/A'],
        ['Truck:', delivery_note.truck.license_plate if delivery_note.truck else 'Not assigned'],
        ['Brand/Color:', f"{delivery_note.truck.brand or 'N/A'} {('(' + delivery_note.truck.color + ')') if delivery_note.truck and delivery_note.truck.color else ''}" if delivery_note.truck else 'N/A'],
        ['Trailer 1:', delivery_note.trailer1.license_plate if delivery_note.trailer1 else 'Not assigned'],
        ['Trailer 2:', delivery_note.trailer2.license_plate if delivery_note.trailer2 else 'Not assigned'],
    ]
    
    vehicle_table = Table(vehicle_data, colWidths=[2.2*inch, 4*inch])
    vehicle_table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
        ('ALIGN', (1, 0), (1, -1), 'LEFT'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e5e7eb')),
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#fafafa')),
        ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#374151')),
        ('TEXTCOLOR', (1, 0), (1, -1), colors.HexColor('#111827')),
    ]))
    
    elements.append(vehicle_table)
    
    # Notes section
    # if delivery_note.notes:
    #     elements.append(Paragraph("Notes", section_style))
    #     notes_para = Paragraph(delivery_note.notes, ParagraphStyle(
    #         'Notes',
    #         parent=styles['Normal'],
    #         fontSize=10,
    #         fontName='Helvetica',
    #         leftIndent=10,
    #         rightIndent=10,
    #         spaceBefore=5,
    #         spaceAfter=10,
    #         borderWidth=1,
    #         borderColor=colors.HexColor('#d1d5db'),
    #         borderPadding=10,
    #         backColor=colors.HexColor('#fffbeb'),
    #         textColor=colors.HexColor('#374151')
    #     ))
    #     elements.append(notes_para)
    
    # Associated weighing records
    weighing_records = delivery_note.weighingrecord_set.all()
    if weighing_records.exists():
        elements.append(Paragraph("Associated Weighing Records", section_style))
        
        records_data = [
            ['ID', 'Date & Time', 'Product', 'Gross Weight', 'Tare Weight', 'Net Weight', 'Unit']
        ]
        
        for record in weighing_records:
            records_data.append([
                str(record.id),
                record.timestamp.strftime('%m/%d/%Y\n%I:%M %p'),
                record.product.name if record.product else 'N/A',
                f"{record.gross_weight:.2f}",
                f"{record.tare_weight:.2f}",
                f"{record.net_weight:.2f}",
                record.unit_of_measure,
                # f"{record.user.first_name} {record.user.last_name}"[:15] + "..." if len(f"{record.user.first_name} {record.user.last_name}") > 15 else f"{record.user.first_name} {record.user.last_name}"
            ])
        
        records_table = Table(records_data, colWidths=[0.6*inch, 1.1*inch, 1.3*inch, 0.9*inch, 0.9*inch, 0.6*inch, 1*inch])
        records_table.setStyle(TableStyle([
            # Header styling
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#374151')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('TOPPADDING', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
             
             # Data rows styling
            ('BACKGROUND', (0, 1), (-1, -1), colors.white),
            ('TEXTCOLOR', (0, 1), (-1, -1), colors.HexColor('#374151')),
            ('ALIGN', (0, 1), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 8),
            ('TOPPADDING', (0, 1), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 8),
            
            # Alternating row colors
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f9fafb')]),
            
            # Grid
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#d1d5db')),
            
            # Alignment for specific columns
            ('ALIGN', (3, 1), (4, -1), 'RIGHT'),  # Weight columns right-aligned
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        
        elements.append(records_table)
    
    # Footer
    elements.append(Spacer(1, 30))
    footer_style = ParagraphStyle(
        'Footer',
        parent=styles['Normal'],
        fontSize=8,
        fontName='Helvetica',
        alignment=TA_CENTER,
        textColor=colors.HexColor('#6b7280'),
        spaceBefore=20
    )
    
    elements.append(Paragraph("─" * 50, footer_style))
    elements.append(Paragraph(f"Generated on {datetime.now().strftime('%B %d, %Y at %I:%M %p')}", footer_style))
    elements.append(Paragraph("IntelliScale Weighing Management System", footer_style))
    
    # Build the PDF
    doc.build(elements)
    
    # Get the value of the buffer
    pdf = buffer.getvalue()
    buffer.close()
    
    # Create the HTTP response
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="delivery_note_{delivery_note.delivery_note_number or delivery_note.id}.pdf"'
    response.write(pdf)
    
    return response

@login_required
def get_delivery_note_record(request, delivery_note_id):
    """Get existing weighing record data for a delivery note (for weighbridge)"""
    if request.method != 'GET':
        return JsonResponse({'success': False, 'message': 'Only GET requests allowed'})
    
    try:
        delivery_note = get_object_or_404(DeliveryNote, pk=delivery_note_id)
        weighing_records = WeighingRecord.objects.filter(delivery_note=delivery_note)
        
        if weighing_records.count() > 1:
            return JsonResponse({
                'success': True,
                'multiple_records': True,
                'message': 'Multiple records found'
            })
        
        if weighing_records.count() == 1:
            record = weighing_records.first()
            return JsonResponse({
                'success': True,
                'multiple_records': False,
                'record': {
                    'barcode': record.barcode,
                    'custom_data': record.custom_data,
                    'gross_weight': float(record.gross_weight),
                    'tare_weight': float(record.tare_weight),
                    'net_weight': float(record.net_weight)
                },
                'product': {
                    'id': record.product.id,
                    'name': record.product.name
                } if record.product else None
            })
        
        # No records found - check if delivery note has a product
        delivery_note_product = None
        if hasattr(delivery_note, 'product') and delivery_note.product:
            delivery_note_product = {
                'id': delivery_note.product.id,
                'name': delivery_note.product.name
            }
        
        return JsonResponse({
            'success': True,
            'multiple_records': False,
            'record': None,
            'product': delivery_note_product
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': str(e)
        })

@login_required
def find_delivery_note_by_barcode(request):
    """Find delivery note by searching for barcode in odoo_data.bales"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Only POST requests allowed'})

    

    try:
        barcode = request.POST.get('barcode', '').strip()
        if not barcode:
            return JsonResponse({'success': False, 'message': 'Barcode is required'})

        # Search for delivery note with this barcode in odoo_data
        delivery_notes = DeliveryNote.objects.all()

        for dnote in delivery_notes:
            if dnote.find_bale_by_barcode(barcode):
                # Check active process for ctl_workflow restriction
                active_process = WeighingProcess.objects.filter(is_active=True).first()
                if active_process and active_process.process_type == 'ctl_workflow':
                    if dnote.get_state() != 'checked':
                        return JsonResponse({
                            'success': False, 
                            'message': f'Delivery Note {dnote.delivery_note_number} has not been checked (Status: {dnote.get_state()}).'
                        })

                # Check if this delivery note is already being scanned by someone else
                # For now, we'll allow only one delivery note to be scanned at a time
                currently_scanned = DeliveryNote.objects.filter(is_being_scanned=True).first()
                
                if currently_scanned and currently_scanned.id != dnote.id:
                    return JsonResponse({
                        'success': False, 
                        'message': f'Another delivery note ({currently_scanned.delivery_note_number}) is currently being scanned'
                    })

                # Check if this specific delivery note can accept this barcode
                if not dnote.can_accept_barcode(barcode):
                    # Check if this is just a rescan of an already-scanned barcode from the same delivery note
                    if dnote.is_scanning_complete() and dnote.find_bale_by_barcode(barcode):
                        # This barcode belongs to this delivery note, allow rescan/update
                        # But only if the delivery note is currently being scanned
                        if not dnote.is_being_scanned:
                            return JsonResponse({
                                'success': False,
                                'message': f'Delivery note {dnote.delivery_note_number} is already complete'
                            })
                    elif dnote.is_scanning_complete():
                        return JsonResponse({
                            'success': False, 
                            'message': f'Delivery note {dnote.delivery_note_number} is already complete'
                        })
                    # Allow re-scans by checking this after can_accept_barcode
                    elif dnote.has_barcode_been_scanned(barcode):
                        # This is a re-scan, so we allow it, but only if delivery note is already being scanned
                        if not dnote.is_being_scanned:
                            return JsonResponse({
                                'success': False, 
                                'message': f'Cannot recall and update. This bale has already been scanned for delivery note {dnote.delivery_note_number}.'
                            })
                    else:
                        return JsonResponse({
                            'success': False, 
                            'message': f'Barcode {barcode} not found in delivery note {dnote.delivery_note_number}'
                        })
                else:
                    # If the delivery note is not being scanned and the barcode can be accepted,
                    # then activate this delivery note for scanning
                    if not dnote.is_being_scanned:
                        # Deactivate any other delivery notes
                        DeliveryNote.objects.filter(is_being_scanned=True).update(is_being_scanned=False)
                        dnote.is_being_scanned = True
                        dnote.save()

                # Determine which total bales to use based on active process settings
                active_process = WeighingProcess.objects.filter(is_active=True).first()  # Get the active process
                if active_process and active_process.allow_bale_insert:
                    total_bales = dnote.get_bale_count_delivered()
                else:
                    total_bales = dnote.get_bale_count()
                
                bale_info = dnote.find_bale_by_barcode(barcode)
                return JsonResponse({
                    'success': True,
                    'delivery_note': {
                        'id': dnote.id,
                        'delivery_note_number': dnote.delivery_note_number,
                        'grower_name': dnote.get_grower_name(),
                        'grower_number': dnote.get_grower_number(),
                        'total_bales': total_bales,
                        'scanned_bales': dnote.scanned_bales_count,
                        'scanned_records_data': dnote.get_scanned_records_data(),
                        'remaining_bales': max(0, total_bales - dnote.scanned_bales_count),
                        'location_name': dnote.get_location_name(),
                        'selling_point_name': dnote.get_selling_point_name(),
                        'preferred_sale_date': dnote.get_preferred_sale_date(),
                        'is_being_scanned': dnote.is_being_scanned
                    },
                    'bale': bale_info,
                    'message': f'Found in delivery note {dnote.delivery_note_number}'
                })
        # Barcode not found in any delivery note
        return JsonResponse({
            'success': False, 
            'message': 'Barcode not found. Please scan the correct bale.'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Error: {str(e)}'
        })

@login_required
def search_delivery_notes(request):
    """Search for open delivery notes to populate the combobox."""
    if request.method != 'GET':
        return JsonResponse({'success': False, 'message': 'Only GET requests allowed'})
    delivery_notes = DeliveryNote.objects.filter(status='Open').order_by('-created_at')
    data = [{'id': note.id, 'text': note.delivery_note_number} for note in delivery_notes]
    return JsonResponse(data, safe=False)

@login_required
def activate_delivery_note(request, pk):
    """Activate a delivery note for scanning."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Only POST requests allowed'})

    try:
        with transaction.atomic():
            # Deactivate any other delivery notes
            DeliveryNote.objects.filter(is_being_scanned=True).update(is_being_scanned=False)
            
            # Activate the selected delivery note
            dnote = get_object_or_404(DeliveryNote, pk=pk)
            
            # Check active process for ctl_workflow restriction
            active_process = WeighingProcess.objects.filter(is_active=True).first()
            if active_process and active_process.process_type == 'ctl_workflow':
                if dnote.get_state() != 'checked':
                    return JsonResponse({
                        'success': False, 
                        'message': f'Delivery Note {dnote.delivery_note_number} has not been checked (Status: {dnote.get_state()}).'
                    })
            
            dnote.is_being_scanned = True
            dnote.save()

            # Determine which total bales to use based on active process settings
            active_process = WeighingProcess.objects.filter(is_active=True).first()  # Get the active process
            if active_process and active_process.allow_bale_insert:
                total_bales = dnote.get_bale_count_delivered()
            else:
                total_bales = dnote.get_bale_count()
            
            return JsonResponse({
                'success': True,
                'delivery_note': {
                    'id': dnote.id,
                    'delivery_note_number': dnote.delivery_note_number,
                    'grower_name': dnote.get_grower_name(),
                    'grower_number': dnote.get_grower_number(),
                    'total_bales': total_bales,
                    'scanned_bales': dnote.scanned_bales_count,
                    'scanned_records_data': dnote.get_scanned_records_data(),
                    'remaining_bales': max(0, total_bales - dnote.scanned_bales_count),
                    'location_name': dnote.get_location_name(),
                    'selling_point_name': dnote.get_selling_point_name(),
                    'preferred_sale_date': dnote.get_preferred_sale_date(),
                    'is_being_scanned': dnote.is_being_scanned
                },
                'message': f'Delivery note {dnote.delivery_note_number} activated for scanning.'
            })

    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)})

@login_required
def driver_create_ajax(request):
    """Create a new driver via AJAX request"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Only POST requests allowed'})

    try:
        form = DriverForm(request.POST)
        if form.is_valid():
            driver = form.save()
            return JsonResponse({
                'success': True,
                'driver': {
                    'id': driver.id,
                    'name': driver.name,
                    'phone': driver.phone or ''
                },
                'message': f'Driver "{driver.name}" created successfully'
            })
        else:
            # Return form errors
            errors = {}
            for field, error_list in form.errors.items():
                errors[field] = error_list[0] if error_list else ''
            return JsonResponse({
                'success': False,
                'errors': errors,
                'message': 'Please correct the errors below'
            })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Error creating driver: {str(e)}'
        })  

@login_required
def recall_and_update_bale(request):
    """
    Handle bale recall and update from the weighing station.
    This will set the bale's mass to 0 in Odoo and then allow for a new weighing.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Invalid request method'})

    try:
        barcode = request.POST.get('barcode', '').strip()
        delivery_note_id = request.POST.get('delivery_note_id')

        if not all([barcode, delivery_note_id]):
            return JsonResponse({'success': False, 'message': 'Barcode and Delivery Note ID are required.'})

        delivery_note = get_object_or_404(DeliveryNote, pk=delivery_note_id)

        # Check if the bale has been scanned for this delivery note
        if not delivery_note.has_barcode_been_scanned(barcode):
            return JsonResponse({'success': False, 'message': 'This bale has not been scanned yet.'})

        # Communicate with ERP to set mass to 0 (recall step)
        company_settings = CompanySettings.objects.first()
        if not company_settings or not company_settings.api_url:
            return JsonResponse({'success': False, 'message': 'ERP settings not configured.'})

        # Get API key from company settings
        api_key = company_settings.api_key if company_settings and company_settings.api_key else None

        # Get the existing weighing record to retrieve hessian value
        weighing_record = WeighingRecord.objects.filter(
            delivery_note=delivery_note,
            barcode=barcode
        ).first()
        
        print(f"Recall and Update: Found weighing record: {weighing_record is not None}")
        scale_id = ''
        hessian_id = ''
        
        if weighing_record:
            # Get scale ID from the existing weighing record
            scale_id = weighing_record.weighing_scale_id or ''
            print(f"Recall and Update: Scale ID: '{scale_id}'")
            
            if weighing_record.custom_data:
                print(f"Recall and Update: Custom data: {weighing_record.custom_data}")
                hessian_id = weighing_record.custom_data.get('hessian_id', '')
                print(f"Recall and Update: Hessian ID: '{hessian_id}'")
        
        # Build the API URL with required parameters like in send_to_erp (only scale_id and hessian_id)
        # Use 2 decimal places format like the weighing records (instead of rounding to whole number)
        base_url = f"{company_settings.api_url}/api/bales/update-mass/?barcode={barcode}&mass=0.01&scale_id={scale_id}"
        if hessian_id:
            api_url = f"{base_url}&hessian_id={hessian_id}"
            print(f"Recall and Update: Using hessian in API URL: {api_url}")
        else:
            api_url = base_url
            print(f"Recall and Update: Using base API URL (no hessian): {api_url}")
        
        # Using requests to communicate with the ERP to set mass to 0
        print(f"Recall and Update: Making API request to: {api_url}")
        headers = {
            "User-Agent": "insomnia/11.5.0",
            "X-API-Key": api_key
        }
        response = requests.post(api_url, headers=headers, timeout=10)
        print(f"Recall and Update: ERP response status: {response.status_code}")
        print(f"Recall and Update: ERP response text: {response.text}")

        if response.status_code == 200:
            # If ERP update is successful, delete the local record so the barcode can be scanned again
            weighing_record = WeighingRecord.objects.filter(
                delivery_note=delivery_note,
                barcode=barcode
            ).first()
            
            if weighing_record:
                # Delete the original weighing record so the barcode becomes available for re-scanning
                weighing_record.delete()
                
                # Since we removed a record, we need to adjust the delivery note's scanned count
                # We need to manually update the scanned_barcodes list to remove this barcode
                if delivery_note.scanned_barcodes and barcode in delivery_note.scanned_barcodes:
                    delivery_note.scanned_barcodes = [b for b in delivery_note.scanned_barcodes if b.strip() != barcode.strip()]
                    # Decrement the scanned count to reflect the removal
                    if delivery_note.scanned_bales_count > 0:
                        delivery_note.scanned_bales_count -= 1
                    delivery_note.save()
                
                # Update the delivery note's scanned records data to reflect the change
                # Determine which total bales to use based on active process settings
                active_process = WeighingProcess.objects.filter(is_active=True).first()  # Get the active process
                if active_process and active_process.allow_bale_insert:
                    total_bales = delivery_note.get_bale_count_delivered()
                else:
                    total_bales = delivery_note.get_bale_count()
                
                response_data = {
                    'success': True,
                    'message': f'Bale {barcode} recalled and removed from local records. You can now scan it again.',
                    'delivery_note': {
                        'scanned_bales': delivery_note.scanned_bales_count,
                        'total_bales': total_bales,
                        'scanned_records_data': delivery_note.get_scanned_records_data(),
                    },
                    'dnote_closed': delivery_note.scanned_bales_count == 0,  # Delivery note closed if all bales done
                    'barcode': barcode  # Include barcode to allow for new weighing
                }
                if response_data['dnote_closed']:
                    response_data['message'] = 'All bales recalled. Delivery note is no longer active.'
                return JsonResponse(response_data)
            else:
                # If no record was found to delete, at least the ERP was updated
                # We just return success but don't modify delivery note counts
                # Determine which total bales to use based on active process settings
                active_process = WeighingProcess.objects.filter(is_active=True).first()  # Get the active process
                if active_process and active_process.allow_bale_insert:
                    total_bales = delivery_note.get_bale_count_delivered()
                else:
                    total_bales = delivery_note.get_bale_count()
                
                response_data = {
                    'success': True,
                    'message': f'Bale {barcode} weight set to 0 in ERP. You can now scan it again for a new weighing.',
                    'delivery_note': {
                        'scanned_bales': delivery_note.scanned_bales_count,
                        'total_bales': total_bales,
                        'scanned_records_data': delivery_note.get_scanned_records_data(),
                    },
                    'dnote_closed': delivery_note.scanned_bales_count == 0,
                    'barcode': barcode  # Include barcode to allow for new weighing
                }
                if response_data['dnote_closed']:
                    response_data['message'] = 'All bales recalled. Delivery note is no longer active.'
                return JsonResponse(response_data)
        else:
            return JsonResponse({
                'success': False,
                'message': f'Failed to update bale in ERP. Status: {response.status_code}, Response: {response.text}'
            })

    except requests.RequestException as e:
        return JsonResponse({'success': False, 'message': f'Error communicating with ERP: {str(e)}'})
    except Exception as e:
        return JsonResponse({'success': False, 'message': f'An unexpected error occurred: {str(e)}'})

@login_required
def truck_create_ajax(request):
    """Create a new truck via AJAX request"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Only POST requests allowed'})

    

    try:
        form = TruckForm(request.POST)
        if form.is_valid():
            truck = form.save()
            return JsonResponse({
                'success': True,
                'truck': {
                    'id': truck.id,
                    'brand': truck.brand,
                    'license_plate': truck.license_plate,
                    'color': truck.color
                },
                'message': f'Truck "{truck.license_plate}" created successfully'
            })
        else:
            # Return form errors
            errors = {}
            for field, error_list in form.errors.items():
                errors[field] = error_list[0] if error_list else ''
            return JsonResponse({
                'success': False,
                'errors': errors,
                'message': 'Please correct the errors below'
            })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Error creating truck: {str(e)}'
        })

@login_required
def trailer_create_ajax(request):
    """Create a new trailer via AJAX request"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Only POST requests allowed'})
    
    try:
        form = TrailerForm(request.POST)
        if form.is_valid():
            trailer = form.save()
            return JsonResponse({
                'success': True,
                'trailer': {
                    'id': trailer.id,
                    'brand': trailer.brand,
                    'license_plate': trailer.license_plate,
                    'color': trailer.color
                },
                'message': f'Trailer "{trailer.license_plate}" created successfully'
            })
        else:
            # Return form errors
            errors = {}
            for field, error_list in form.errors.items():
                errors[field] = error_list[0] if error_list else ''
            return JsonResponse({
                'success': False,
                'errors': errors,
                'message': 'Please correct the errors below'
            })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Error creating trailer: {str(e)}'
        })

@login_required
def delivery_note_create_ajax(request):
    """Create a new delivery note via AJAX request"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'Only POST requests allowed'})
    
    try:
        form = DeliveryNoteForm(request.POST)
        if form.is_valid():
            delivery_note = form.save(commit=False)
            delivery_note.created_by = request.user

            # Generate automatic delivery note number
            delivery_note.delivery_note_number = generate_delivery_note_number()
            delivery_note.save()

            return JsonResponse({
                'success': True,
                'delivery_note': {
                    'id': delivery_note.id,
                    'delivery_note_number': delivery_note.delivery_note_number,
                    'status': delivery_note.status,
                    'driver': delivery_note.driver.name if delivery_note.driver else None,
                    'truck': delivery_note.truck.license_plate if delivery_note.truck else None,
                    'trailer1': delivery_note.trailer1.license_plate if delivery_note.trailer1 else None,
                    'trailer2': delivery_note.trailer2.license_plate if delivery_note.trailer2 else None,
                    'product': delivery_note.product.name if delivery_note.product else None,
                    'notes': delivery_note.notes
                },
                'message': f'Delivery note "{delivery_note.delivery_note_number}" created successfully'
            })
        else:
            # Return form errors
            errors = {}
            for field, error_list in form.errors.items():
                errors[field] = error_list[0] if error_list else ''
            return JsonResponse({
                'success': False,
                'errors': errors,
                'message': 'Please correct the errors below'
            })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'message': f'Error creating delivery note: {str(e)}'
        })

@login_required
def printing_station(request):
    """
    View for the local printing station.
    - Handles scale interaction (via existing endpoints)
    - Manages PrintingNotes and PrintingRecords
    - purely local, no ERP/Odoo interaction
    """
    if request.method == 'POST':
        # Handle form submission
        printing_note_id = request.POST.get('printing_note_id')
        grower_number = request.POST.get('grower_number')
        first_name = request.POST.get('first_name')
        last_name = request.POST.get('last_name')
        
        # Scale/Record Data
        scale_id = request.POST.get('scale_id')
        product_id = request.POST.get('product_id')
        barcode = request.POST.get('barcode', '').strip()
        gross_weight = request.POST.get('gross_weight') or 0
        tare_weight = request.POST.get('tare_weight') or 0
        net_weight = request.POST.get('net_weight') or 0
        
        # Find or Create Note
        printing_note = None
        if printing_note_id:
            try:
                printing_note = PrintingNote.objects.get(id=printing_note_id, user=request.user)
            except PrintingNote.DoesNotExist:
                pass # Should not happen unless tampering or new session
        
        if not printing_note:
            # Create new note
            printing_note = PrintingNote.objects.create(
                user=request.user,
                grower_number=grower_number,
                first_name=first_name,
                last_name=last_name
            )
        else:
            # Maybe update grower info if changed? 
            # Requirements didn't specify, but usually user might correct a name.
            # Let's update it.
            if grower_number: printing_note.grower_number = grower_number
            if first_name: printing_note.first_name = first_name
            if last_name: printing_note.last_name = last_name
            printing_note.save() # Updates updated_at
            
        # Update Session with Active Note ID
        request.session['active_printing_note_id'] = printing_note.id
            
        # Create Record
        product = None
        if product_id:
            try:
                product = Product.objects.get(id=product_id)
            except Product.DoesNotExist:
                pass
                
        PrintingRecord.objects.create(
            printing_note=printing_note,
            scale_id=scale_id,
            product=product,
            barcode=barcode,
            gross_weight=gross_weight,
            tare_weight=tare_weight,
            net_weight=net_weight,
            unit_of_measure='kg' # Default for now
        )
        
        messages.success(request, f"Recorded {barcode} ({net_weight} kg)")
        return redirect('scale:printing_station')

    else:
        # GET Request
        new_session = request.GET.get('new_session')
        printing_note = None
        
        if new_session == 'true':
            # Intentional new session, clear active note in session
            if 'active_printing_note_id' in request.session:
                del request.session['active_printing_note_id']
        else:
            # Try to resume from session
            active_note_id = request.session.get('active_printing_note_id')
            if active_note_id:
                try:
                    printing_note = PrintingNote.objects.get(id=active_note_id, user=request.user)
                except PrintingNote.DoesNotExist:
                    # Session ID invalid (maybe deleted), clear it
                    del request.session['active_printing_note_id']
            
        # Create a dictionary of product tare weights for the template
        product_tare_weights = {}
        processed_products = Product.objects.filter(is_active=True)
        for product in processed_products:
            product_tare_weights[product.id] = float(product.tare_weight or 0)

        context = {
            'scales': Scale.objects.filter(is_active=True),
            'products': processed_products,
            'printing_note': printing_note,
            'product_tare_weights': product_tare_weights
            # Pass today's date for display if needed
        }
        return render(request, 'scale/printing_station.html', context)

@login_required
def printing_note_list(request):
    """
    List all printing notes.
    """
    notes_list = PrintingNote.objects.all().order_by('-created_at')
    paginator = Paginator(notes_list, 20) # Show 20 notes per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    return render(request, 'scale/printing_note_list.html', {'page_obj': page_obj})

@login_required
def printing_note_detail(request, pk):
    """
    Detail view for a printing note.
    """
    note = get_object_or_404(PrintingNote, pk=pk)
    return render(request, 'scale/printing_note_detail.html', {'note': note})

@login_required
def printing_record_delete(request, pk):
    record = get_object_or_404(PrintingRecord, pk=pk)
    note_id = record.printing_note.id
    
    if request.method == 'POST':
        barcode = record.barcode
        record.delete()
        messages.success(request, f'Record for barcode {barcode} deleted successfully.')
    
    return redirect('scale:printing_note_detail', pk=note_id)