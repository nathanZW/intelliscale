"""
Weighing station views for IntelliScale.
Handles the main weighing station interface and ERP synchronization.
"""
from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from users.views import is_admin
from ..models import (
    Scale, WeighingProcess, Product, DeliveryNote, WeighingRecord, 
    CompanySettings, Driver, Truck, Trailer
)
import json
from decimal import Decimal
import requests
import logging

logger = logging.getLogger(__name__)


@login_required
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
    active_process = None
    weight_rounding = 2
    
    if processes.exists():
        # Get from the first process (or you could use specific logic to choose which process)
        active_process = processes.first()
    else:
        # Default to weighbridge if no active process found
        active_process = WeighingProcess.objects.filter(process_type='WeighBridge').first()

    if active_process:
        min_weight = active_process.min_weight
        max_weight = active_process.max_weight
        allow_manual_entry = active_process.allow_manual_entry
        weight_rounding = active_process.weight_rounding
    
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
                try:
                    process = WeighingProcess.objects.get(pk=process_id)
                    allow_spaces = process.allow_spaces_in_barcode
                except WeighingProcess.DoesNotExist:
                    messages.error(request, f'Weighing process with ID {process_id} not found.')
                    return redirect('scale:weighing_station')
                
            if allow_spaces:
                barcode = raw_barcode # Don't strip if spaces are allowed
            else:
                barcode = raw_barcode.strip()
                
            weighing_record_id = request.POST.get('weighing_record_id') # Get the ID for update

            logger.debug('Net Weight: %s', net_weight)
            
            # Convert weights to Decimal, handling empty strings
            gross_weight = Decimal(gross_weight) if gross_weight and gross_weight.strip() else Decimal('0')
            tare_weight = Decimal(tare_weight) if tare_weight and tare_weight.strip() else Decimal('0')
            net_weight = Decimal(net_weight) if net_weight and net_weight.strip() else Decimal('0')
            
            if min_weight is not None and max_weight is not None:
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
            if process and process.allow_marshalling:
                # For marshalling, we might want to enhance the custom_data with
                # additional validation or processing for lot_number and group_number
                pass
            
            # Handle delivery note association
            delivery_note = None
            delivery_note_id = request.POST.get('delivery_note_id')
            # process is already fetched above
            
            # Handle CTL Workflow logic
            if process and process.process_type in ['ctl_workflow', 'ctl_commercial_workflow']:
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
            
            logger.debug('FINAL — Net: %s, Gross: %s, Tare: %s', net_weight, gross_weight, tare_weight)
            
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
                    scale = get_object_or_404(Scale, pk=scale_id)
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
                        messages.success(request, f'Weighing record created successfully: {barcode}')
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
                else:
                    pass
                
                # Check if scanning is complete
                if delivery_note.is_scanning_complete():
                    # Do not set is_being_scanned = False here. User must confirm closure.
                    delivery_note.save()
                else:
                    remaining = delivery_note.get_remaining_bales_count()
                    pass
            
            print_after_save = request.POST.get('print_after_save') == 'true'
            
            if print_after_save:
                # Update print count
                weighing_record.print_count += 1
                weighing_record.last_printed_at = timezone.now()
                weighing_record.save()
                pass
                
            return redirect('scale:weighing_station')
            
        except Exception as e:
            logger.exception('Unexpected error creating weighing record')
            messages.error(request, f'Error creating weighing record: {str(e)}')
            return redirect('scale:weighing_station')
    
    # Get currently active delivery note for CTL workflow
    active_delivery_note = DeliveryNote.objects.filter(is_being_scanned=True).first()
    
    if active_delivery_note:
        active_delivery_note.scanned_records_data = active_delivery_note.get_scanned_records_data()
        
        # Determine the appropriate bale count to display based on the active process
        # If there are processes and the first one allows bale insert, use delivered bale count
        # active_process is already determined at the start of the view
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
    process_rolling_grower_number_config = {}
    process_auto_save_on_scan = {}
    for process in processes:
        process_allow_bale_insert[process.id] = process.allow_bale_insert
        process_allow_spaces_in_barcode[process.id] = process.allow_spaces_in_barcode
        process_use_code39_mod43_validation[process.id] = process.use_code39_mod43_validation
        process_rolling_hessian_config[process.id] = process.rolling_hessian
        process_rolling_grower_number_config[process.id] = getattr(process, 'rolling_grower_number', False)
        process_auto_save_on_scan[process.id] = process.auto_save_on_scan

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

    # Rolling Grower Number Logic
    prefilled_grower_number_value = ''
    if active_process and getattr(active_process, 'rolling_grower_number', False):
        # The people using rolling grower number don't use delivery notes
        # So we just get the latest weighing record for this process
        last_record_process = WeighingRecord.objects.filter(
            process_id=active_process.id
        ).order_by('-timestamp').first()
        
        if last_record_process and last_record_process.custom_data:
            if 'grower_number' in last_record_process.custom_data:
                prefilled_grower_number_value = last_record_process.custom_data['grower_number']
    
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
        'process_rolling_grower_number_config': json.dumps(process_rolling_grower_number_config),
        'process_auto_save_on_scan': json.dumps(process_auto_save_on_scan),
        'unsynced_count': WeighingRecord.objects.filter(is_synced=False).count(),
        'allow_manual_entry': allow_manual_entry,
        'active_delivery_note': active_delivery_note,
        'product_tare_weights': json.dumps(product_tare_weights),
        'prefilled_hessian_value': prefilled_hessian_value,
        'prefilled_grower_number_value': prefilled_grower_number_value,
    }
    
    return render(request, 'scale/weighing_station.html', context)


def _log_sync_error(weighing_record_id, error_message):
    """Helper to update a weighing record with an error"""
    logger.error('Sync error: %s', error_message)
    if weighing_record_id:
        WeighingRecord.objects.filter(id=weighing_record_id).update(
            is_synced=False,
            last_sync_attempt=timezone.now(),
            sync_error_message=error_message
        )
    return False

def _log_sync_success(weighing_record_id):
    """Helper to update a weighing record with success"""
    if weighing_record_id:
        WeighingRecord.objects.filter(id=weighing_record_id).update(
            is_synced=True,
            last_sync_attempt=timezone.now(),
            sync_error_message=''
        )
    return True

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

    # Get company settings
    company_settings = CompanySettings.objects.first()
    
    # Get API key from company settings
    api_key = company_settings.api_key if company_settings and company_settings.api_key else None
    
    # Check for a session id in the browser cookies
    session_id = request.COOKIES.get('session_id')
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
                "Content-Type": "application/json",
                "X-API-Key": api_key
            }

            logger.debug('Making authentication request to: %s', url)
            logger.debug('Auth headers: %s', headers)
            logger.debug('Auth payload: %s', payload)

            response = requests.request("POST", url, json=payload, headers=headers)

            logger.debug('Auth response status: %s', response.status_code)
            logger.debug('Auth response headers: %s', dict(response.headers))
            logger.debug('Auth response cookies: %s', dict(response.cookies))
            logger.debug('Auth response body: %s', response.text)
        
            if response.status_code == 200:
                result = response.json()
                if 'result' in result and result['result'] is not None:
                    # Get new session_id from cookies
                    new_session_id = response.cookies.get('session_id')
                    logger.debug('New Session ID: %s', new_session_id)
                    if new_session_id:
                        session_id = new_session_id
                    else:
                        # If no session_id in response but no error, try to get it from the result
                        session_id = result['result'].get('session_id')
                        
                    if session_id:
                        logger.info('Successfully authenticated with ERP session ID: %s', session_id)
                    else:
                        return _log_sync_error(weighing_record_id, "Authentication succeeded but no valid session ID returned from ERP")
                elif 'error' in result:
                    error_message = result['error'].get('data', {}).get('message', 'Unknown authentication error')
                    return _log_sync_error(weighing_record_id, f"ERP authentication failed: {error_message}")
                else:
                    return _log_sync_error(weighing_record_id, f"ERP authentication failed: Unexpected response format - {response.text}")
            else:
                return _log_sync_error(weighing_record_id, f"ERP authentication failed with status {response.status_code}: {response.text}")
                
        except requests.exceptions.ConnectionError as e:
            return _log_sync_error(weighing_record_id, f"ERP connection error during authentication: {str(e)}")
        except requests.exceptions.Timeout as e:
            return _log_sync_error(weighing_record_id, f"ERP timeout error during authentication: {str(e)}")
        except ValueError as e:  # JSON decode error
            return _log_sync_error(weighing_record_id, f"Error parsing ERP authentication response: {str(e)}")
        except Exception as e:
            return _log_sync_error(weighing_record_id, f"Error during ERP authentication: {str(e)}")
    
    
    if company_settings.erp_system.name == 'Odoo':
        # Check if allow_bale_insert is enabled for this process
        allow_bale_insert_enabled = False
        if process_id:
            try:
                process = WeighingProcess.objects.get(id=process_id)
                allow_bale_insert_enabled = process.allow_bale_insert
            except WeighingProcess.DoesNotExist:
                logger.warning('Process with id %s not found', process_id)
        
        # If allow_bale_insert is enabled, call create-commercial-bale endpoint
        if allow_bale_insert_enabled:
            try:
                delivery_note_number = ''
                if weighing_record_id:
                    # Just fetch delivery note number for payload
                    weighing_record = WeighingRecord.objects.filter(id=weighing_record_id).select_related('delivery_note').first()
                    if weighing_record and weighing_record.delivery_note:
                        delivery_note_number = weighing_record.delivery_note.delivery_note_number
                
                if not delivery_note_number and delivery_note:  
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
                
                logger.debug('Calling create-commercial-bale with params: %s', params)
                
                payload = ""
                
                # Make the API call
                headers = {
                    "User-Agent": "insomnia/11.5.0",
                    "X-API-Key": api_key
                }
                response = requests.request("POST", url, data=payload, headers=headers, params=params)
                
                logger.debug('create-commercial-bale response status: %s', response.status_code)
                logger.debug('create-commercial-bale response text: %s', response.text)
                
                if response.status_code in [200, 201]:  # 201 Created is also successful
                    # Check if the response body contains success=false
                    try:
                        response_json = response.json()
                        if isinstance(response_json, dict) and response_json.get('success') is False:
                            # The API returned 200 but with success=false in the body
                            return _log_sync_error(weighing_record_id, f"create-commercial-bale failed: {response_json.get('message', response.text)}")
                        else:
                            return _log_sync_success(weighing_record_id)
                    except ValueError:
                        return _log_sync_success(weighing_record_id)
                elif response.status_code >= 400:
                    error_message = response.text
                    try:
                        response_json = response.json()
                        if 'error' in response_json and 'data' in response_json['error']:
                            error_message = response_json['error']['data'].get('message', response.text)
                    except (ValueError, KeyError):
                        pass
                    return _log_sync_error(weighing_record_id, f"create-commercial-bale failed with status {response.status_code}: {error_message}")
                else:
                    return _log_sync_error(weighing_record_id, f"create-commercial-bale returned unexpected status {response.status_code}: {response.text}")
                    
            except requests.exceptions.ConnectionError as e:
                return _log_sync_error(weighing_record_id, f"Connection error calling create-commercial-bale: {str(e)}")
            except requests.exceptions.Timeout as e:
                return _log_sync_error(weighing_record_id, f"Timeout error calling create-commercial-bale: {str(e)}")
            except Exception as e:
                return _log_sync_error(weighing_record_id, f"Error calling create-commercial-bale: {str(e)}")
        else:
            # Use existing logic for other cases
            logger.debug('Sending barcode %s, net weight %s, scale id %s to ERP', barcode, net_weight, scale_id)
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
                        grower_number = custom_data.get('grower_number', '') if custom_data else ''
                        if not grower_number:
                            grower_number = ''
                        base_url = f"{company_settings.api_url}/receiving/scaleserver/manual_scale/{float(net_weight):.2f}/{barcode}/{scale_id}/{grower_number}"
                        logger.debug('Calling manual_scale with base_url: %s', base_url)
                    
                    payload = ""
                    headers = {
                        "User-Agent": "insomnia/11.5.0",
                        "X-API-Key": api_key
                    }
                    response = requests.request("POST", base_url, data=payload, params=params, headers=headers)
                    logger.debug('Calling update-mass with params: %s', params)
                    logger.debug('update-mass response status: %s', response.status_code)
                    logger.debug('update-mass response text: %s', response.text)
                    
                    if response.status_code in [200, 201]:  # 201 Created is also successful
                        try:
                            response_json = response.json()
                            if isinstance(response_json, dict) and response_json.get('success') is False:
                                return _log_sync_error(weighing_record_id, f"ERP API call failed: {response_json.get('message', response.text)}")
                            else:
                                return _log_sync_success(weighing_record_id)
                        except ValueError:
                            return _log_sync_success(weighing_record_id)
                    elif response.status_code >= 400:
                        error_message = response.text
                        try:
                            response_json = response.json()
                            if 'error' in response_json and 'data' in response_json['error']:
                                error_message = response_json['error']['data'].get('message', response.text)
                        except (ValueError, KeyError):
                            pass
                        return _log_sync_error(weighing_record_id, f"ERP API call failed with status {response.status_code}: {error_message}")
                    else:
                        return _log_sync_error(weighing_record_id, f"ERP API call returned unexpected status {response.status_code}: {response.text}")
                except requests.exceptions.ConnectionError as e:
                    return _log_sync_error(weighing_record_id, f"Connection error during ERP API call: {str(e)}")
                except requests.exceptions.Timeout as e:
                    return _log_sync_error(weighing_record_id, f"Timeout error during ERP API call: {str(e)}")
                except Exception as e:
                    return _log_sync_error(weighing_record_id, f"Error sending to erp: {str(e)}")
            else:
                return _log_sync_error(weighing_record_id, "No valid session ID available for ERP communication")
    else:
        # TODO: Add other erp systems here
        logger.warning('ERP system %s is not supported. Only Odoo is currently supported.', company_settings.erp_system.name)
        return False


@login_required
@user_passes_test(is_admin)
def sync_all_unsynced(request):
    logger.info('Syncing all unsynced weighing records')
    weighing_records = WeighingRecord.objects.filter(is_synced=False)
    for weighing_record in weighing_records:
        logger.info('Syncing weighing record: %s', weighing_record.id)
        success = send_to_erp(weighing_record.barcode, weighing_record.net_weight, weighing_record.weighing_scale_id, weighing_record.id, request, weighing_record.custom_data, weighing_record.process.process_type, weighing_record.process.id, weighing_record.delivery_note)
        
        if success and weighing_record.delivery_note:
            allow_spaces = False
            if weighing_record.process:
                allow_spaces = weighing_record.process.allow_spaces_in_barcode
            
            weighing_record.delivery_note.add_scanned_barcode(weighing_record.barcode, allow_spaces=allow_spaces)
            weighing_record.delivery_note.save()
    return redirect('scale:weighing_record_list')
