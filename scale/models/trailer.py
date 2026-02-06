from django.db import models


class Trailer(models.Model):
    brand = models.CharField(max_length=100, blank=True, null=True)
    license_plate = models.CharField(max_length=100)
    color = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return self.license_plate
