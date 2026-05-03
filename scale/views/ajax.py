"""
AJAX endpoint views for IntelliScale.
Handles AJAX requests for creating drivers, trucks, trailers, delivery notes,
and bale recall/update operations.
"""
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from ..models import (
    DeliveryNote, WeighingRecord, CompanySettings, WeighingProcess,
    Driver, Truck, Trailer
)
from ..forms import DriverForm, TruckForm, TrailerForm, DeliveryNoteForm
from .delivery_note import generate_delivery_note_number
import requests


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
        raw_barcode = request.POST.get('barcode', '')
        active_process = WeighingProcess.objects.filter(is_active=True).first()
        allow_spaces = active_process.allow_spaces_in_barcode if active_process else False
        barcode = raw_barcode if allow_spaces else raw_barcode.strip()
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
        
        # Use the recall-bale endpoint instead of update-mass for complete 0 mass
        api_url = f"{company_settings.api_url}/api/bales/recall-bale/"
        params = {'barcode': barcode, 'scale_id': scale_id}
        if hessian_id:
            params['hessian_id'] = hessian_id

        print(f"Recall and Update: Making API request to: {api_url} with params: {params}")
        headers = {
            "User-Agent": "insomnia/11.5.0",
            "X-API-Key": api_key
        }
        response = requests.post(api_url, headers=headers, params=params, timeout=10)
        print(f"Recall and Update: ERP response status: {response.status_code}")
        print(f"Recall and Update: ERP response text: {response.text}")

        if response.status_code == 200:
            # BUG-005 FIX: If Odoo state is laid, make another API call to set it to checked
            if delivery_note.odoo_data and delivery_note.odoo_data.get('state') == 'laid':
                status_url = f"{company_settings.api_url}/api/grower-delivery-notes/update-status"
                status_params = {
                    "document_number": delivery_note.delivery_note_number,
                    "status": "checked"
                }
                try:
                    status_res = requests.post(status_url, params=status_params, headers=headers, timeout=10)
                    if status_res.status_code in [200, 201]:
                        delivery_note.odoo_data['state'] = 'checked'
                        delivery_note.status = 'Open'
                        delivery_note.save()
                except Exception as e:
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.error('Failed to change status back to checked during recall_and_update_bale: %s', e)

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
                        'id': delivery_note.id,
                        'delivery_note_number': delivery_note.delivery_note_number,
                        'grower_name': delivery_note.get_grower_name(),
                        'grower_number': delivery_note.get_grower_number(),
                        'location_name': delivery_note.get_location_name(),
                        'selling_point_name': delivery_note.get_selling_point_name(),
                        'preferred_sale_date': delivery_note.get_preferred_sale_date(),
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
                        'id': delivery_note.id,
                        'delivery_note_number': delivery_note.delivery_note_number,
                        'grower_name': delivery_note.get_grower_name(),
                        'grower_number': delivery_note.get_grower_number(),
                        'location_name': delivery_note.get_location_name(),
                        'selling_point_name': delivery_note.get_selling_point_name(),
                        'preferred_sale_date': delivery_note.get_preferred_sale_date(),
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
