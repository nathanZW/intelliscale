from django.db import models
from django.db.models import Sum

from users.models import CustomUser


class PrintingNote(models.Model):
    grower_name = models.CharField(max_length=100, blank=True)
    grower_number = models.CharField(max_length=100, blank=True)
    first_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    expected_bales = models.IntegerField(null=True, blank=True, help_text="Total expected number of barcodes/bales")
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Note {self.id} - {self.grower_name or self.grower_number or 'No Name'} ({self.created_at.strftime('%Y-%m-%d')})"

    def get_total_gross_weight(self):
        return self.records.aggregate(total=Sum('gross_weight'))['total'] or 0

    def get_total_net_weight(self):
        return self.records.aggregate(total=Sum('net_weight'))['total'] or 0
