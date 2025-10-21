# your_app/tasks.py
from celery import shared_task
import requests
import logging
from django.utils import timezone
from .models import DeliveryNote, CompanySettings

logger = logging.getLogger(__name__)

@shared_task
def sync_odoo_delivery_notes():
    """Fetch all delivery notes from Odoo and sync with Django"""
    try:
        # Get company settings for API URL
        company_settings = CompanySettings.objects.first()
        if not company_settings or not company_settings.api_url:
            logger.error("No company settings found or API URL not configured")
            return "Error: API URL not configured"
        
        # Fetch from Odoo API
        response = requests.get(
            f'{company_settings.api_url}/api/grower-delivery-notes',
            params={'include_bales': 'true',
                    'state': 'open,checked,laid'},
            headers={
                'User-Agent': 'insomnia/11.5.0',
                # Add cookie authentication if needed
                # 'Cookie': 'your-session-cookie-here'
            },
            timeout=30
        )
        
        if response.status_code != 200:
            error_message = (
                f"Odoo API error: Status {response.status_code} for URL {response.url}. "
                f"Response: {response.text}"
            )
            logger.error(error_message)
            return f"API Error: {response.status_code}"
        
        odoo_data = response.json()
        
        if not odoo_data.get('success'):
            logger.error("Odoo API returned success=false")
            return "API returned error"
        
        synced_count = 0
        error_count = 0
        
        # Process each delivery note
        for item in odoo_data['data']:
            try:
                sync_single_delivery_note(item)
                synced_count += 1
            except Exception as e:
                logger.error(f"Error syncing record {item.get('id')}: {str(e)}")
                error_count += 1
        
        result = f"Synced: {synced_count}, Errors: {error_count}"
        logger.info(result)
        return result
        
    except Exception as e:
        logger.error(f"Sync task failed: {str(e)}")
        return f"Task failed: {str(e)}"

def sync_single_delivery_note(odoo_record):
    """Sync a single delivery note record"""
    try:
        odoo_id = odoo_record['id']
        document_number = odoo_record['document_number']
        
        # Get or create delivery note
        delivery_note, created = DeliveryNote.objects.get_or_create(
            odoo_id=odoo_id,
            defaults={
                'delivery_note_number': document_number,
                'created_by_id': 1,  # Set a default user or handle this properly
            }
        )
        
        # Update with Odoo data
        delivery_note.odoo_data = odoo_record
        delivery_note.delivery_note_number = document_number
        # Note: partner_id is IntegerField, but grower_number is string like "V342819"
        # Store grower_number in odoo_data, extract numeric part if needed
        grower_number = odoo_record.get('grower_number', '')
        if grower_number.startswith('V') and grower_number[1:].isdigit():
            delivery_note.partner_id = int(grower_number[1:])  # Extract numeric part
        delivery_note.is_synced = True
        delivery_note.last_sync_attempt = timezone.now()
        delivery_note.sync_error_message = ''
        
        # Map Odoo state to your status
        if odoo_record['state'] in ['open', 'checked', 'laid']:
            delivery_note.status = 'Open'
        else:  # 'closed'
            delivery_note.status = 'Closed'
        
        delivery_note.save()
        
        action = "Created" if created else "Updated"
        logger.info(f"{action} delivery note: {document_number}")
        
    except Exception as e:
        # Log error but don't crash the whole sync
        logger.error(f"Failed to sync record {odoo_record.get('id')}: {str(e)}")
        
        # Try to update error message if record exists
        try:
            if 'odoo_id' in locals():
                DeliveryNote.objects.filter(odoo_id=odoo_id).update(
                    sync_error_message=str(e),
                    last_sync_attempt=timezone.now(),
                    is_synced=False
                )
        except:
            pass
        
        raise  # Re-raise to be caught by parent function

@shared_task
def check_completed_delivery_notes():
    """Check for delivery notes where all bales have been scanned and update their status"""
    try:
        # Find delivery notes that are fully scanned but still active
        completed_dnotes = DeliveryNote.objects.filter(
            is_being_scanned=False,  # Not currently being scanned
            status='Open',  # Still marked as open
            scanned_bales_count__gt=0  # Has some scanned bales
        )

        updated_count = 0
        error_count = 0
        
        for dnote in completed_dnotes:
            try:
                # Check if all bales are scanned and Odoo state is still 'checked'
                odoo_state = dnote.odoo_data.get('state', '').lower()
                if dnote.is_scanning_complete() and odoo_state == 'checked':
                    # Update status to completed and send notification to Odoo
                    success = update_dnote_completion_status(dnote)
                    if success:
                        dnote.status = 'Closed'
                        dnote.save()
                        updated_count += 1
                        logger.info(f"Updated delivery note {dnote.delivery_note_number} to completed status")
                    else:
                        error_count += 1
                        logger.error(f"Failed to update Odoo status for delivery note {dnote.delivery_note_number}")
                        
            except Exception as e:
                logger.error(f"Error processing delivery note {dnote.delivery_note_number}: {str(e)}")
                error_count += 1
        
        result = f"Completed check: {updated_count} updated, {error_count} errors"
        logger.info(result)
        return result
        
    except Exception as e:
        logger.error(f"Completed delivery notes check task failed: {str(e)}")
        return f"Task failed: {str(e)}"

def update_dnote_completion_status(delivery_note):
    """Send a request to Odoo to update the delivery note status to laid"""
    try:
        # Get company settings for API URL
        company_settings = CompanySettings.objects.first()
        if not company_settings or not company_settings.api_url:
            logger.error("No company settings found or API URL not configured")
            return False
        
        url = f"{company_settings.api_url}/api/grower-delivery-notes/update-status"
        
        querystring = {
            "document_number": delivery_note.delivery_note_number,
            "status": "laid"
        }
        
        payload = ""
        
        headers = {
            "cookie": "frontend_lang=en_GB",
            "User-Agent": "insomnia/11.5.0"
        }
        
        response = requests.request("POST", url, data=payload, headers=headers, params=querystring)
        
        if response.status_code in [200, 201]:
            logger.info(f"Successfully updated Odoo status to 'laid' for delivery note {delivery_note.delivery_note_number}")
            return True
        else:
            logger.error(f"Failed to update Odoo status. Status: {response.status_code}, Response: {response.text}")
            return False
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Network error when updating Odoo status for {delivery_note.delivery_note_number}: {str(e)}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error updating Odoo status for {delivery_note.delivery_note_number}: {str(e)}")
        return False