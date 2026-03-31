from django.db import models


class Scale(models.Model):
    PROTOCOL_GENERIC = 'generic'
    PROTOCOL_METTLER_TOLEDO = 'mettler_toledo'
    PROTOCOL_CAS_STREAM = 'cas_stream'
    PROTOCOL_CHOICES = [
        (PROTOCOL_GENERIC, 'Generic'),
        (PROTOCOL_METTLER_TOLEDO, 'Mettler Toledo'),
        (PROTOCOL_CAS_STREAM, 'CAS Stream'),
    ]

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
    protocol = models.CharField(
        max_length=32,
        choices=PROTOCOL_CHOICES,
        default=PROTOCOL_GENERIC,
        help_text="Select the serial protocol used by this scale.",
    )
    mettler_toledo = models.BooleanField(default=False, help_text="Enable scale connection that may help with Mettler Toledo scales")

    def save(self, *args, **kwargs):
        self.mettler_toledo = self.protocol == self.PROTOCOL_METTLER_TOLEDO

        update_fields = kwargs.get('update_fields')
        if update_fields is not None and 'protocol' in update_fields:
            kwargs['update_fields'] = list(set(update_fields) | {'mettler_toledo'})

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.model_number})"
