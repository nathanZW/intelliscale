from django.db import models

from users.models import CustomUser
from .scale import Scale
from .weighing_process import WeighingProcess
from .product import Product


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
