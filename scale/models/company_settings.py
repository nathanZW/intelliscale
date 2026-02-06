from django.db import models

from .erp_system import ErpSystem


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
