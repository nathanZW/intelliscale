from django.db import models
import qrcode
from io import BytesIO
from django.core.files import File

from users.models import CustomUser
from .driver import Driver
from .truck import Truck
from .trailer import Trailer
from .product import Product


class DeliveryNote(models.Model):
    delivery_note_number = models.CharField(max_length=100, blank=True)
    created_by = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    status = models.CharField(max_length=100, blank=True, choices=[('Open', 'Open'), ('Closed', 'Closed')], default='Open')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_synced = models.BooleanField(default=False)
    last_sync_attempt = models.DateTimeField(null=True, blank=True)
    sync_error_message = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    
    partner_id = models.IntegerField(null=True, blank=True)
    odoo_data = models.JSONField(default=dict, blank=True)
    odoo_id = models.IntegerField(null=True, blank=True, unique=True)
    
    # CTL Workflow fields
    is_being_scanned = models.BooleanField(default=False, help_text="True when this delivery note is currently active in ctl_workflow")
    scanned_bales_count = models.IntegerField(default=0, help_text="Number of bales scanned for this delivery note")
    scanned_barcodes = models.JSONField(default=list, blank=True, help_text="List of barcodes that have been scanned for this delivery note")
    
    driver = models.ForeignKey(Driver, on_delete=models.CASCADE, blank=True, null=True)
    truck = models.ForeignKey(Truck, on_delete=models.CASCADE, blank=True, null=True)
    trailer1 = models.ForeignKey(Trailer, on_delete=models.CASCADE, related_name='trailer1', blank=True, null=True)
    trailer2 = models.ForeignKey(Trailer, on_delete=models.CASCADE, related_name='trailer2', blank=True, null=True)
    qr_code = models.ImageField(upload_to='qr_codes/', blank=True, null=True)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, blank=True, null=True)
    
    
    def get_scanned_records_data(self):
        """
        Returns a list of dictionaries: [{'barcode': '...', 'weight_display': '...kg', 'group_number': '...', 'lot_number': '...'}]
        for all scanned barcodes associated with this delivery note, ordered by scan time.
        """
        # Import here to avoid circular import
        from .weighing_record import WeighingRecord
        
        # Get all WeighingRecords for this delivery note that have a barcode
        # We use .prefetch_related() to get the full records with custom_data
        records_dict = {}
        weighing_records = WeighingRecord.objects.filter(
            delivery_note=self,
            barcode__in=self.scanned_barcodes
        ).order_by('timestamp')
        
        # Create a dict mapping barcode to the weighing record
        for record in weighing_records:
            # Format net_weight to one decimal place (e.g., 14.2kg)
            net_weight_str = f"{record.net_weight:.1f}{record.unit_of_measure}"
            records_dict[record.barcode] = {
                'weight_display': net_weight_str,
                'custom_data': record.custom_data
            }
        
        # Re-order the results based on the original scanned_barcodes list order
        result = []
        for barcode in self.scanned_barcodes:
            # Get bale information for additional data like group_number and lot_number
            bale_info = self.find_bale_by_barcode(barcode)
            
            if barcode in records_dict:
                # Use weighing record's custom data (user input) as primary source
                custom_data = records_dict[barcode]['custom_data']
                record_data = {
                    'barcode': barcode,
                    'weight_display': records_dict[barcode]['weight_display'],
                    # Prioritize weighing record data over odoo data
                    'lot_number': custom_data.get('lot_number', ''),
                    'group_number': custom_data.get('group_number', '')
                }
            else:
                # If no weighing record exists, check if we can get weight data from odoo_data
                if bale_info and 'weight' in bale_info:
                    weight = bale_info['weight']
                    unit = bale_info.get('unit_of_measure', 'kg')  # Default to kg if not specified
                    record_data = {
                        'barcode': barcode,
                        'weight_display': f"{weight}{unit}"
                    }
                else:
                    # This might happen if there was an error creating the weighing record
                    # Default to 0.0kg, though ideally all scanned barcodes should have weighing records
                    record_data = {
                        'barcode': barcode,
                        'weight_display': 'Pending'
                    }
            
            # Override with data from odoo_data only if weighing record doesn't have it
            # (This maintains backward compatibility and provides fallback data)
            if bale_info:
                if not record_data.get('group_number') and 'group_number' in bale_info:
                    record_data['group_number'] = bale_info.get('group_number', '')
                if not record_data.get('lot_number') and 'lot_number' in bale_info:
                    record_data['lot_number'] = bale_info.get('lot_number', '')
            
            result.append(record_data)
        
        return result

    def save(self, *args, **kwargs):
        # Generate QR code if it doesn't exist on save
        if not self.qr_code or self.pk is None:
            self.generate_qr_code()
        super().save(*args, **kwargs)

    def generate_qr_code(self):
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(self.delivery_note_number)
        qr.make(fit=True)

        # Create QR code image
        img = qr.make_image(fill_color="black", back_color="white")
        
        # Save to BytesIO
        buffer = BytesIO()
        img.save(buffer, format='PNG')
        buffer.seek(0)
        
        # Save to model field
        filename = f'qr_{self.delivery_note_number}_{self.pk or "new"}.png'
        self.qr_code.save(filename, File(buffer), save=False)
        buffer.close()
        
    def __str__(self):
        return f"{self.delivery_note_number}"
    
    def get_grower_name(self):
        """Get grower name from Odoo data"""
        return self.odoo_data.get('grower_name', '')
    
    def get_grower_number(self):
        """Get grower number from Odoo data"""
        return self.odoo_data.get('grower_number', '')
    
    def get_bales(self):
        """Get bales list from Odoo data"""
        return self.odoo_data.get('bales', [])
    
    def get_total_mass(self):
        """Get total mass from Odoo data"""
        return self.odoo_data.get('total_mass', 0)
    
    def get_bale_count(self):
        """Get number of bales - if associated process allows bale insert, use number_of_bales_delivered instead"""
        return self._get_total_bales_for_process()
    
    def get_bale_count_delivered(self):
        """Get number of bales delivered"""
        return self.odoo_data.get('number_of_bales_delivered', 0)
    
    def get_percentage_completion(self):
        """Get percentage completion"""
        return self.odoo_data.get('percentage_completion', 0)
    
    def get_location_name(self):
        """Get location name"""
        return self.odoo_data.get('location_name', '')
    
    def get_selling_point_name(self):
        """Get selling point name"""
        return self.odoo_data.get('selling_point_name', '')
    
    def get_preferred_sale_date(self):
        """Get preferred sale date"""
        return self.odoo_data.get('preferred_sale_date', '')
    
    def get_state(self):
        """Get Odoo state"""
        return self.odoo_data.get('state', '')
    
    @property
    def is_odoo_synced(self):
        """Check if this record came from Odoo"""
        return self.odoo_id is not None
    
    def find_bale_by_barcode(self, barcode, allow_spaces=False):
        """Find a bale in odoo_data by scale_barcode"""
        # Trim whitespace from input barcode unless allow_spaces is True
        if allow_spaces:
            barcode = str(barcode) if barcode else ''
        else:
            barcode = str(barcode).strip() if barcode else ''
            
        bales = self.odoo_data.get('bales', [])
        
        for bale in bales:
            scale_barcode = str(bale.get('scale_barcode', ''))
            if not allow_spaces:
                scale_barcode = scale_barcode.strip()
                
            if scale_barcode == barcode:
                return bale
        return None
    
    def _get_total_bales_for_process(self):
        """Helper method to get the appropriate total bales based on the associated process"""
        # Since there's no direct relationship between DeliveryNote and WeighingProcess,
        # we'll look for the first associated WeighingRecord to determine the process
        first_weighing_record = self.weighingrecord_set.first()
        if first_weighing_record and first_weighing_record.process and first_weighing_record.process.allow_bale_insert:
            # If bale insert is allowed, use number of bales delivered
            return self.odoo_data.get('number_of_bales_delivered', 0)
        else:
            # Otherwise, use the original number of bales
            return self.odoo_data.get('number_of_bales', 0)
    
    def get_remaining_bales_count(self):
        """Get number of bales remaining to be scanned"""
        total_bales = self._get_total_bales_for_process()
        return max(0, total_bales - self.scanned_bales_count)
    
    def is_scanning_complete(self):
        """Check if all bales have been scanned"""
        return self.get_remaining_bales_count() == 0
    
    def can_accept_barcode(self, barcode, allow_spaces=False):
        """Check if this delivery note can accept the given barcode"""
        if self.is_scanning_complete():
            return False
        if self.has_barcode_been_scanned(barcode, allow_spaces=allow_spaces):
            return False  # Already scanned
        return self.find_bale_by_barcode(barcode, allow_spaces=allow_spaces) is not None
    
    def has_barcode_been_scanned(self, barcode, allow_spaces=False):
        """Check if a barcode has already been scanned"""
        if allow_spaces:
            barcode = str(barcode) if barcode else ''
            # Check exact match only
            return barcode in self.scanned_barcodes
        else:
            barcode = str(barcode).strip() if barcode else ''
            # Check both trimmed and original barcodes for safety
            return barcode in self.scanned_barcodes or any(str(b).strip() == barcode for b in self.scanned_barcodes)
    
    def add_scanned_barcode(self, barcode, allow_spaces=False):
        """Add a barcode to the list of scanned barcodes"""
        # Trim whitespace before storing unless allow_spaces is True
        if allow_spaces:
            processed_barcode = str(barcode) if barcode else ''
        else:
            processed_barcode = str(barcode).strip() if barcode else ''
            
        if not self.has_barcode_been_scanned(processed_barcode, allow_spaces=allow_spaces):
            self.scanned_barcodes.append(processed_barcode)
            self.scanned_bales_count += 1
            return True  # New barcode added
        return False  # Already existed

    def recall_bale(self, barcode, allow_spaces=False):
        """
        Recalls a bale by removing its barcode from the scanned list,
        decrementing the scanned count, and deleting the weighing record.
        """
        # Import here to avoid circular import
        from .weighing_record import WeighingRecord
        
        if allow_spaces:
            processed_barcode = str(barcode) if barcode else ''
        else:
            processed_barcode = str(barcode).strip() if barcode else ''
            
        if self.has_barcode_been_scanned(processed_barcode, allow_spaces=allow_spaces):
            # Remove the barcode
            if allow_spaces:
                self.scanned_barcodes = [b for b in self.scanned_barcodes if str(b) != processed_barcode]
            else:
                self.scanned_barcodes = [b for b in self.scanned_barcodes if str(b).strip() != processed_barcode]
            
            # Decrement the count
            if self.scanned_bales_count > 0:
                self.scanned_bales_count -= 1
            
            # Delete the corresponding weighing record
            WeighingRecord.objects.filter(
                delivery_note=self,
                barcode=processed_barcode
            ).delete()
            
            self.save()
            return True  # Recall was successful
        return False  # Barcode was not found or not scanned
