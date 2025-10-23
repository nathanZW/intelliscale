from django.db import models
from users.models import CustomUser
import qrcode
from io import BytesIO
from django.core.files import File
from PIL import Image
from django.conf import settings

class Scale(models.Model):
    name = models.CharField(max_length=100)
    scale_id = models.CharField(max_length=100, unique=True, blank=True, null=True, help_text="Unique identifier for the scale. Can be changed by an admin.")
    com_port = models.CharField(max_length=50, blank=True, null=True)
    baud_rate = models.IntegerField(default=9600)
    timeout = models.IntegerField(default=1)
    parity = models.CharField(max_length=10, choices=[('N', 'None'), ('E', 'Even'), ('O', 'Odd')], default='N')
    stop_bits = models.IntegerField(default=1)
    data_bits = models.IntegerField(default=8)
    manufacturer = models.CharField(max_length=50, blank=True, null=True)
    model_number = models.CharField(max_length=50, blank=True, null=True)
    max_capacity = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True, help_text="Maximum weight capacity in kg")
    is_active = models.BooleanField(default=True)
    last_connection_status = models.CharField(max_length=50, default='disconnected', blank=True, null=True)
    last_seen = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    tare_weight = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    
    def __str__(self):
        return f"{self.name} ({self.model_number})"


class ScaleIdHistory(models.Model):
    """Logs changes to the scale_id of a Scale."""
    scale = models.ForeignKey(Scale, on_delete=models.CASCADE, related_name='id_history')
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    change_date = models.DateTimeField(auto_now_add=True)
    old_id = models.CharField(max_length=100)
    new_id = models.CharField(max_length=100)

    class Meta:
        ordering = ['-change_date']
    
    
class WeighingProcess(models.Model):
    WEIGHT_ROUNDING_CHOICES = [
        (3, '0.001'),
        (2, '0.01'),
        (1, '0.1'),
        (0, '1.0'),
    ]
    
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    custom_fields_schema = models.JSONField(default=list, blank=True)
    erp_target_model = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    max_weight = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    min_weight = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    weight_rounding = models.IntegerField(blank=True, null=True, choices=WEIGHT_ROUNDING_CHOICES, default=2)
    allow_manual_entry = models.BooleanField(default=False)
    process_type = models.CharField(max_length=100, blank=True, null=True, choices=[('WeighBridge', 'WeighBridge'),('Manual', 'Manual'), ('Automated', 'Automated'), ('ctl_workflow', 'CTL Workflow'), ('ctl_commercial_workflow', 'CTL Commercial Workflow')], default='WeighBridge')
    allow_marshalling = models.BooleanField(default=False)
    allow_bale_insert = models.BooleanField(default=False)
    
    def __str__(self):
        return self.name

    

class Product(models.Model):
    erp_product_id = models.CharField(max_length=100, blank=True)
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    tare_weight = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    
    def __str__(self):
        return self.name
    

class WeighingRecord(models.Model):
    scale = models.ForeignKey(Scale, on_delete=models.CASCADE)
    weighing_scale_id = models.CharField(max_length=100, blank=True, help_text="The ID of the scale at the time of weighing.")
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    process = models.ForeignKey(WeighingProcess, on_delete=models.CASCADE)
    barcode = models.CharField(max_length=100, blank=True)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(auto_now_add=True)
    gross_weight = models.DecimalField(max_digits=10, decimal_places=2)
    tare_weight = models.DecimalField(max_digits=10, decimal_places=2)
    net_weight = models.DecimalField(max_digits=10, decimal_places=2)
    unit_of_measure = models.CharField(max_length=50)
    custom_data = models.JSONField(default=dict, blank=True)
    notes = models.TextField(blank=True)
    is_synced = models.BooleanField(default=False)
    erp_record_id = models.CharField(max_length=100, blank=True)
    erp_model_name = models.CharField(max_length=100, blank=True)
    last_sync_attempt = models.DateTimeField(null=True, blank=True)
    sync_error_message = models.TextField(blank=True)
    print_count = models.IntegerField(default=0)
    last_printed_at = models.DateTimeField(null=True, blank=True)
    delivery_note = models.ForeignKey('DeliveryNote', on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.scale.name} - {self.product.name} - {self.timestamp}"


class Driver(models.Model):
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return self.name

class Truck(models.Model):
    brand = models.CharField(max_length=100, blank=True, null=True)
    license_plate = models.CharField(max_length=100)
    color = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.license_plate
    
class Trailer(models.Model):
    brand = models.CharField(max_length=100, blank=True, null=True)
    license_plate = models.CharField(max_length=100)
    color = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return self.license_plate    

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
        # Get all WeighingRecords for this delivery note that have a barcode
        # We use .values() for efficiency and order by timestamp to prioritize the latest record
        # if a barcode somehow has multiple records (though logic should prevent this)
        records = WeighingRecord.objects.filter(
            delivery_note=self,
            barcode__in=self.scanned_barcodes
        ).values('barcode', 'net_weight', 'unit_of_measure').order_by('timestamp')
        
        # Create a map for quick lookup, prioritizing the latest record if duplicates exist
        barcode_map = {}
        for record in records:
            # Format net_weight to one decimal place (e.g., 14.2kg)
            net_weight_str = f"{record['net_weight']:.1f}{record['unit_of_measure']}"
            barcode_map[record['barcode']] = net_weight_str
        
        # Re-order the results based on the original scanned_barcodes list order
        result = []
        for barcode in self.scanned_barcodes:
            # Get bale information for additional data like group_number and lot_number
            bale_info = self.find_bale_by_barcode(barcode)
            
            if barcode in barcode_map:
                record_data = {
                    'barcode': barcode,
                    'weight_display': barcode_map[barcode]
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
            
            # Add group_number and lot_number if available in bale_info
            if bale_info:
                if 'group_number' in bale_info:
                    record_data['group_number'] = bale_info.get('group_number', '')
                if 'lot_number' in bale_info:
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
        """Get number of bales"""
        return self.odoo_data.get('number_of_bales', 0)
    
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
    
    def find_bale_by_barcode(self, barcode):
        """Find a bale in odoo_data by scale_barcode"""
        # Trim whitespace from input barcode
        barcode = str(barcode).strip() if barcode else ''
        bales = self.odoo_data.get('bales', [])
        
        for bale in bales:
            scale_barcode = str(bale.get('scale_barcode', '')).strip()
            if scale_barcode == barcode:
                return bale
        return None
    
    def get_remaining_bales_count(self):
        """Get number of bales remaining to be scanned"""
        total_bales = self.odoo_data.get('number_of_bales', 0)
        return max(0, total_bales - self.scanned_bales_count)
    
    def is_scanning_complete(self):
        """Check if all bales have been scanned"""
        return self.get_remaining_bales_count() == 0
    
    def can_accept_barcode(self, barcode):
        """Check if this delivery note can accept the given barcode"""
        if self.is_scanning_complete():
            return False
        if self.has_barcode_been_scanned(barcode):
            return False  # Already scanned
        return self.find_bale_by_barcode(barcode) is not None
    
    def has_barcode_been_scanned(self, barcode):
        """Check if a barcode has already been scanned"""
        barcode = str(barcode).strip() if barcode else ''
        # Check both trimmed and original barcodes for safety
        return barcode in self.scanned_barcodes or any(str(b).strip() == barcode for b in self.scanned_barcodes)
    
    def add_scanned_barcode(self, barcode):
        """Add a barcode to the list of scanned barcodes"""
        # Trim whitespace before storing
        trimmed_barcode = str(barcode).strip() if barcode else ''
        if not self.has_barcode_been_scanned(trimmed_barcode):
            self.scanned_barcodes.append(trimmed_barcode)
            self.scanned_bales_count += 1
            return True  # New barcode added
        return False  # Already existed

    def recall_bale(self, barcode):
        """
        Recalls a bale by removing its barcode from the scanned list,
        decrementing the scanned count, and deleting the weighing record.
        """
        trimmed_barcode = str(barcode).strip() if barcode else ''
        if self.has_barcode_been_scanned(trimmed_barcode):
            # Remove the barcode
            self.scanned_barcodes = [b for b in self.scanned_barcodes if str(b).strip() != trimmed_barcode]
            
            # Decrement the count
            if self.scanned_bales_count > 0:
                self.scanned_bales_count -= 1
            
            # Delete the corresponding weighing record
            WeighingRecord.objects.filter(
                delivery_note=self,
                barcode=trimmed_barcode
            ).delete()
            
            self.save()
            return True  # Recall was successful
        return False  # Barcode was not found or not scanned
    

class ErpSystem(models.Model):
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return self.name
    
    
class CompanySettings(models.Model):
    company_name = models.CharField(max_length=100)
    erp_system = models.ForeignKey(ErpSystem, on_delete=models.CASCADE)
    api_key = models.CharField(max_length=100, blank=True, null=True)
    erp_username = models.CharField(max_length=100, blank=True, null=True)
    erp_password = models.CharField(max_length=100, blank=True, null=True)
    api_url = models.CharField(max_length=100, blank=True, null=True)
    database_name = models.CharField(max_length=100, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.company_name} - {self.erp_system}"
    
    

