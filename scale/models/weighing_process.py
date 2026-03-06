from django.db import models


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
    allow_spaces_in_barcode = models.BooleanField(default=False, help_text="If enabled, spaces in barcodes will not be stripped (e.g. for Code 39 Mod 43)")
    use_code39_mod43_validation = models.BooleanField(default=False, help_text="If enabled, validates scanned barcodes using Code 39 Mod 43 algorithm.")
    rolling_hessian = models.BooleanField(default=False, help_text="If enabled, pre-populates the hessian value from the previous weighing record for the same delivery note.")
    rolling_grower_number = models.BooleanField(default=False, help_text="If enabled, pre-populates the grower number from the previous weighing record for this process.")
    auto_save_on_scan = models.BooleanField(default=False, help_text="If enabled, scanning a barcode will automatically trigger a save.")
    
    def __str__(self):
        return self.name
