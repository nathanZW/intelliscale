"""
Delivery note management views for IntelliScale.
Handles delivery note CRUD, synchronization with Odoo, bale recall, and PDF generation.
"""
from django.db import transaction
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from django.core.cache import cache
from users.views import is_admin
from ..models import (
    DeliveryNote, WeighingRecord, CompanySettings, WeighingProcess,
    Driver, Truck, Trailer, Product
)
from ..forms import DeliveryNoteForm
from ..tasks import sync_odoo_delivery_notes
import requests
import logging
from datetime import datetime
from decimal import Decimal

logger = logging.getLogger(__name__)


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
                with transaction.atomic():
                    WeighingRecord.objects.filter(delivery_note=delivery_note).delete()
                    # Reset scanned count and other local stats
                    delivery_note.scanned_barcodes = []
                    delivery_note.scanned_bales_count = 0
                    delivery_note.status = 'Open'
                    delivery_note.is_being_scanned = False
                    if delivery_note.odoo_data and isinstance(delivery_note.odoo_data, dict):
                        delivery_note.odoo_data['state'] = 'checked'
                    delivery_note.save()
                    
                messages.success(request, "Delivery note recalled successfully. Weighing records have been deleted.")
            else:
                error_msg = data.get('message', 'Unknown error from external API')
                messages.error(request, f"Failed to recall delivery note: {error_msg}")
                logger.error('Failed to recall delivery note %s: %s', delivery_note.delivery_note_number, error_msg)
                
        except requests.RequestException as e:
            messages.error(request, f"Network error while recalling delivery note: {str(e)}")
            logger.error('Network error recalling delivery note %s: %s', delivery_note.delivery_note_number, e)
        except Exception as e:
            messages.error(request, f"An unexpected error occurred: {str(e)}")
            logger.exception('Unexpected error recalling delivery note %s', delivery_note.delivery_note_number)
            
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
    if request.method == 'POST':
        logger.info('[SYNC] Manual delivery note sync triggered by user: %s', request.user)
        try:
            # Check if another sync is already running by checking the same lock
            lock_id = "sync_odoo_delivery_notes_lock"
            if cache.get(lock_id):
                # Another sync is already running
                logger.info('[SYNC] Skip: Another sync is already running.')
                return JsonResponse({
                    'success': False,
                    'message': 'Another sync is currently running, please wait for it to complete.'
                })

            # Call the sync task asynchronously
            task_result = sync_odoo_delivery_notes.delay()
            logger.info('[SYNC] Task initiated successfully. Task ID: %s', task_result.id)

            # Return success response
            return JsonResponse({
                'success': True,
                'message': 'Delivery note sync initiated successfully.',
                'task_id': str(task_result.id)  # Include task ID for potential tracking
            })
        except Exception as e:
            logger.exception('[SYNC] Error initiating sync')
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



def _handle_status_update_response(response, delivery_note):
    '''Helper to handle external API responses for status updates.'''
    if response.status_code in [200, 201]:
        try:
            response_json = response.json()
            if isinstance(response_json, dict) and response_json.get('success') is False:
                error_message = response_json.get('message', response.text)
                logger.error('Odoo API returned success=false for delivery note %s. Error: %s', delivery_note.delivery_note_number, error_message)
                return False
            else:
                logger.info("Successfully updated Odoo status to 'laid' for delivery note %s", delivery_note.delivery_note_number)
                return True
        except ValueError:
            logger.info("Successfully updated Odoo status to 'laid' for delivery note %s (non-JSON response)", delivery_note.delivery_note_number)
            return True
    elif response.status_code >= 400:
        error_message = response.text
        try:
            response_json = response.json()
            if 'error' in response_json and 'data' in response_json['error']:
                error_message = response_json['error']['data'].get('message', response.text)
        except (ValueError, KeyError):
            pass
        logger.error('Failed to update Odoo status. Status: %s. Error: %s', response.status_code, error_message)
        return False
    else:
        logger.error('Unexpected response from Odoo. Status: %s. Response: %s', response.status_code, response.text)
        return False

def update_dnote_completion_status_with_api_key(delivery_note):
    """Send a request to Odoo to update the delivery note status to laid, using API key"""
    try:
        # Get company settings for API URL and API key
        company_settings = CompanySettings.objects.first()
        if not company_settings or not company_settings.api_url:
            logger.error('No company settings found or API URL not configured')
            return False

        # Get API key from company settings
        api_key = company_settings.api_key if company_settings and company_settings.api_key else None
        if not api_key:
            logger.error('API key not configured in company settings')
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

        return _handle_status_update_response(response, delivery_note)

    except requests.exceptions.ConnectionError as e:
        logger.error('Connection error when updating Odoo status for %s: %s', delivery_note.delivery_note_number, e)
        return False
    except requests.exceptions.Timeout as e:
        logger.error('Timeout error when updating Odoo status for %s: %s', delivery_note.delivery_note_number, e)
        return False
    except requests.exceptions.RequestException as e:
        logger.error('Request error when updating Odoo status for %s: %s', delivery_note.delivery_note_number, e)
        return False
    except Exception as e:
        logger.exception('Unexpected error updating Odoo status for %s', delivery_note.delivery_note_number)
        return False


@login_required
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
                
                logger.debug('Making authentication request to: %s', auth_url)
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
                            logger.debug('Successfully authenticated with ERP session ID: %s', session_id)
                        else:
                            return JsonResponse({
                                'success': False,
                                'message': 'Authentication succeeded but no valid session ID returned from ERP'
                            })
                    elif 'error' in auth_result:
                        error_message = auth_result['error'].get('data', {}).get('message', 'Unknown authentication error')
                        logger.error('ERP authentication error in recall_bale: %s', error_message)
                        return JsonResponse({
                            'success': False,
                            'message': f'ERP authentication failed: {error_message}'
                        })
                    else:
                        logger.error('Failed to authenticate in recall_bale - unexpected response format: %s', auth_response.text)
                        return JsonResponse({
                            'success': False,
                            'message': f'ERP authentication failed: Unexpected response format'
                        })
                else:
                    logger.error('Failed to authenticate in recall_bale, status %s: %s', auth_response.status_code, auth_response.text)
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
            
            def success_action():
                delivery_note.scanned_barcodes = [b for b in delivery_note.scanned_barcodes if b != barcode]
                if delivery_note.scanned_bales_count > 0:
                    delivery_note.scanned_bales_count -= 1
                delivery_note.save()
                WeighingRecord.objects.filter(
                    delivery_note=delivery_note,
                    barcode=barcode
                ).delete()
                
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
                            # It's now open again since a bale was removed
                            delivery_note.status = 'Open'
                            delivery_note.save()
                    except Exception as e:
                        logger.error('Failed to change status back to checked during recall_bale: %s', e)

                return JsonResponse({
                    'success': True,
                    'message': f'Bale {barcode} has been successfully recalled.',
                    'scanned_count': delivery_note.scanned_bales_count,
                    'total_count': delivery_note.get_bale_count()
                })
                
            return _handle_external_api_response(response, success_action, delivery_note)
                
        except requests.exceptions.ConnectionError as e:
            logger.error('Connection error during recall_bale: %s', e)
            return JsonResponse({
                'success': False,
                'message': f'Connection error communicating with Odoo: {str(e)}'
            })
        except requests.exceptions.Timeout as e:
            logger.error('Timeout error during recall_bale: %s', e)
            return JsonResponse({
                'success': False,
                'message': f'Timeout error communicating with Odoo: {str(e)}'
            })
        except requests.RequestException as e:
            logger.error('Request error during recall_bale: %s', e)
            return JsonResponse({
                'success': False,
                'message': f'Error communicating with Odoo: {str(e)}'
            })
        except Exception as e:
            logger.exception('Unexpected error in recall_bale')
            return JsonResponse({
                'success': False,
                'message': f'Unexpected error: {str(e)}'
            })
    
    return JsonResponse({
        'success': False,
        'message': 'Invalid request method.'
    })


def _handle_external_api_response(response, success_action, delivery_note):
    """Helper to handle external API responses and format the JsonResponse."""
    if response.status_code in [200, 201]:
        try:
            response_json = response.json()
            if isinstance(response_json, dict) and response_json.get('success') is False:
                error_message = response_json.get('message', 'External API returned failure.')
                return JsonResponse({'success': False, 'message': error_message})
        except ValueError:
            pass
            
        return success_action()
    elif response.status_code >= 400:
        error_message = f"API Error {response.status_code}: {response.text}"
        try:
            response_json = response.json()
            if isinstance(response_json, dict) and 'message' in response_json:
                error_message = response_json['message']
            elif 'error' in response_json and 'data' in response_json['error']:
                error_message = response_json['error']['data'].get('message', response.text)
        except (ValueError, KeyError):
            pass
        return JsonResponse({'success': False, 'message': error_message})
    else:
        return JsonResponse({
            'success': False,
            'message': f'Unexpected response from Odoo. Status: {response.status_code}. Response: {response.text}'
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
        url = f"{company_settings.api_url}/api/grower-delivery-notes/close-marshalled-at-scale"
        
        # Prepare parameters
        params = {
            'document_number': delivery_note.delivery_note_number
        }
        
        headers = {
            "User-Agent": "insomnia/11.5.0",
            "X-API-Key": api_key
        }
        
        logger.debug('Calling close-marshalled-at-scale with params: %s', params)
        
        # Make the API call
        response = requests.post(url, params=params, headers=headers, timeout=10)
        
        logger.debug('close-marshalled-at-scale response status: %s', response.status_code)
        logger.debug('close-marshalled-at-scale response text: %s', response.text)
        
        def success_action():
            # If successful, close the delivery note locally
            delivery_note.status = 'Closed'
            delivery_note.is_being_scanned = False
            delivery_note.save()
            
            return JsonResponse({
                'success': True,
                'message': f'Delivery note {delivery_note.delivery_note_number} closed successfully.'
            })
            
        return _handle_external_api_response(response, success_action, delivery_note)
            
    except requests.Timeout as e:
        logger.error('Timeout error during close_commercial_delivery_note: %s', e)
        return JsonResponse({
            'success': False,
            'message': f'Timeout error communicating with external API: {str(e)}'
        })
    except requests.RequestException as e:
        logger.error('Request error during close_commercial_delivery_note: %s', e)
        return JsonResponse({
            'success': False,
            'message': f'Error communicating with external API: {str(e)}'
        })
    except Exception as e:
        logger.exception('Unexpected error in close_commercial_delivery_note')
        return JsonResponse({
            'success': False,
            'message': f'Unexpected error: {str(e)}'
        })


@login_required
@user_passes_test(is_admin)
def print_delivery_note(request, pk):
    """Generate and return a professionally styled PDF for the delivery note"""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from io import BytesIO
    import os
    from django.conf import settings
    
    delivery_note = get_object_or_404(DeliveryNote, pk=pk)
    
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
    qr_img = None
    if delivery_note.qr_code and delivery_note.qr_code.name:
        try:
            # Get the full path to the QR code file in media folder
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
        ['Created By:', f"{delivery_note.created_by.first_name} {delivery_note.created_by.last_name}"],
        ['Created Date:', delivery_note.created_at.strftime('%B %d, %Y at %I:%M %p')],
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
    elements.append(Paragraph("Vehicle &amp; Driver Information", section_style))
    
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
@user_passes_test(is_admin)
def print_dispatch_note(request, pk=None):
    """Generate and return a professionally styled bulk PDF for CONFIRMATION OF DISPATCH"""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.platypus import BaseDocTemplate, PageTemplate, Frame, Table, TableStyle, Paragraph, Spacer, KeepTogether, PageBreak
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from io import BytesIO
    from datetime import datetime
    
    # Get list of delivery notes
    ids_str = request.GET.get('ids', '')
    if pk:
        delivery_notes = [get_object_or_404(DeliveryNote, pk=pk)]
    elif ids_str:
        ids_list = [int(i.strip()) for i in ids_str.split(',') if i.strip().isdigit()]
        delivery_notes = list(DeliveryNote.objects.filter(id__in=ids_list))
        if not delivery_notes:
            return HttpResponse("No valid delivery notes selected.", status=400)
    else:
        return HttpResponse("No delivery note specified.", status=400)

    buffer = BytesIO()
    doc = BaseDocTemplate(
        buffer, 
        pagesize=landscape(A4),
        leftMargin=0.5*inch,
        rightMargin=0.5*inch,
        topMargin=0.5*inch,
        bottomMargin=0.5*inch
    )
    
    styles = getSampleStyleSheet()
    
    # Styles
    company_name_style = ParagraphStyle('CompanyName', parent=styles['Normal'], fontSize=12, fontName='Helvetica-Bold', textColor=colors.black)
    company_address_style = ParagraphStyle('CompanyAddress', parent=styles['Normal'], fontSize=10, fontName='Helvetica', textColor=colors.black)
    title_style = ParagraphStyle('DocTitle', parent=styles['Normal'], fontSize=18, fontName='Helvetica-Bold', textColor=colors.black, alignment=TA_RIGHT)
    dispatch_info_style = ParagraphStyle('DispatchInfo', parent=styles['Normal'], fontSize=10, fontName='Helvetica-Bold', textColor=colors.black, alignment=TA_RIGHT)
    dispatch_val_style = ParagraphStyle('DispatchVal', parent=styles['Normal'], fontSize=10, fontName='Helvetica', textColor=colors.black, alignment=TA_RIGHT)
    meta_label_style = ParagraphStyle('MetaLabel', parent=styles['Normal'], fontSize=11, fontName='Helvetica-Bold')
    grower_header_style = ParagraphStyle('GrowerHeader', parent=styles['Normal'], fontSize=12, fontName='Helvetica-Bold', spaceBefore=15, spaceAfter=5, textColor=colors.HexColor('#1f2937'))
    
    def dispatch_header(canvas, doc):
        canvas.saveState()
        # Top-left company info
        canvas.setFont('Helvetica-Bold', 12)
        canvas.drawString(0.5*inch, 7.8*inch, "CURVERID TOBACCO (PVT) LTD")
        canvas.setFont('Helvetica', 10)
        canvas.drawString(0.5*inch, 7.6*inch, "28 SIMON MAZORODZE, SOUTHERTON")
        canvas.drawString(0.5*inch, 7.45*inch, "+263 242 620243")
        
        # Top-right title
        canvas.setFont('Helvetica-Bold', 18)
        canvas.drawRightString(11.19*inch, 7.8*inch, "CONFIRMATION OF DISPATCH")
        
        # Dispatch info
        # Aggregate totals from the query
        total_scanned = sum(dn.scanned_bales_count for dn in delivery_notes)
        total_expected = sum(dn.get_bale_count() or dn.scanned_bales_count for dn in delivery_notes)
        # Use earliest dispatch created_at/updated_at or current date
        if delivery_notes:
            dispatch_date = delivery_notes[0].updated_at.strftime('%m/%d/%Y')
        else:
            dispatch_date = datetime.now().strftime('%m/%d/%Y')
        
        canvas.setFont('Helvetica-Bold', 10)
        canvas.drawRightString(10.0*inch, 7.45*inch, "Dispatch Date:")
        canvas.drawRightString(10.0*inch, 7.25*inch, "Dispatch Ref:")
        canvas.drawRightString(10.0*inch, 7.05*inch, "Bales:")
        
        canvas.setFont('Helvetica', 10)
        canvas.drawRightString(11.19*inch, 7.45*inch, dispatch_date)
        canvas.drawRightString(11.19*inch, 7.25*inch, "________________") # Pen
        canvas.drawRightString(11.19*inch, 7.05*inch, f"{total_scanned}/{total_expected}")
        
        # Driver info
        canvas.setFont('Helvetica-Bold', 11)
        canvas.drawString(0.5*inch, 7.0*inch, "Driver:")
        canvas.drawString(1.5*inch, 7.0*inch, "________________________________")
        canvas.drawString(4.5*inch, 7.0*inch, "Horse:")
        canvas.drawString(5.5*inch, 7.0*inch, "________________________")
        
        canvas.drawString(4.5*inch, 6.7*inch, "Trailer:")
        canvas.drawString(5.5*inch, 6.7*inch, "________________________")
        
        # Bottom Line Header
        canvas.line(0.5*inch, 6.5*inch, 11.19*inch, 6.5*inch)
        
        canvas.restoreState()
    
    # We create two page templates:
    # 1. First Page: Has the header, so frames start lower down.
    # Landscape A4 is 11.69 x 8.27 inches.
    left_frame_first = Frame(0.5*inch, 0.5*inch, 5.1*inch, 5.8*inch, id='col1_first')
    right_frame_first = Frame(6.09*inch, 0.5*inch, 5.1*inch, 5.8*inch, id='col2_first')
    first_page = PageTemplate(id='FirstPage', frames=[left_frame_first, right_frame_first], onPage=dispatch_header)
    
    # 2. Later Pages: No header, full height frames.
    left_frame_later = Frame(0.5*inch, 0.5*inch, 5.1*inch, 7.27*inch, id='col1_later')
    right_frame_later = Frame(6.09*inch, 0.5*inch, 5.1*inch, 7.27*inch, id='col2_later')
    later_pages = PageTemplate(id='LaterPages', frames=[left_frame_later, right_frame_later])
    
    doc.addPageTemplates([first_page, later_pages])

    elements = []

    # Process all selected delivery notes and group by grower number.
    grower_data = {}
    
    for dn in delivery_notes:
        grower_no = str(dn.get_grower_number() or "Unknown Grower")
        loc_name = str(dn.get_location_name() or "")
        records = dn.get_scanned_records_data()
        
        if grower_no not in grower_data:
            grower_data[grower_no] = []
            
        for rd in records:
            mass_clean = rd.get('weight_display', '').replace('kg', '').strip()
            row = [
                rd.get('barcode', ''),
                str(rd.get('group_number', '')),
                str(rd.get('lot_number', '')),
                mass_clean,
                loc_name
            ]
            grower_data[grower_no].append(row)

    # Sort growers
    sorted_growers = sorted(list(grower_data.keys()))
    
    # Determine column widths for single table (5.1 inches total width per frame)
    col_widths = [1.5*inch, 0.7*inch, 0.7*inch, 0.9*inch, 1.3*inch]
    headers = ["Barcode", "Grp", "Lot", "Mass", "Location"]
    
    for grower in sorted_growers:
        bales = grower_data[grower]
        if not bales:
            continue
            
        elements.append(Paragraph(f"Grower No: {grower}", grower_header_style))
        
        table_data = [headers] + bales
        
        t = Table(table_data, colWidths=col_widths, repeatRows=1)
        t.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('LINEBELOW', (0, 0), (-1, 0), 1, colors.black),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        
        elements.append(t)
        elements.append(Spacer(1, 10))

    if not elements:
        elements.append(Paragraph("No bales found for the selected delivery notes.", styles['Normal']))

    # Flow into document
    doc.build(elements)
    
    pdf = buffer.getvalue()
    buffer.close()
    
    filename_suffix = "bulk" if not pk else str(pk)
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="dispatch_note_{filename_suffix}.pdf"'
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
        logger.exception('Unexpected error in get_delivery_note_record for delivery_note_id=%s', delivery_note_id)
        return JsonResponse({
            'success': False,
            'message': 'An unexpected error occurred.'
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
                active_process = WeighingProcess.objects.filter(is_active=True).first()
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
