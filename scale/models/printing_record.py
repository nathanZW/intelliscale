from django.db import models

from .printing_note import PrintingNote
from .product import Product


class PrintingRecord(models.Model):
    printing_note = models.ForeignKey(PrintingNote, on_delete=models.CASCADE, related_name='records')
    barcode = models.CharField(max_length=100, blank=True)
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True)
    scale_id = models.CharField(max_length=100, blank=True)
    gross_weight = models.DecimalField(max_digits=10, decimal_places=2)
    tare_weight = models.DecimalField(max_digits=10, decimal_places=2)
    net_weight = models.DecimalField(max_digits=10, decimal_places=2)
    moisture = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    unit_of_measure = models.CharField(max_length=50, default='kg')
    timestamp = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.barcode} - {self.net_weight}{self.unit_of_measure}"
