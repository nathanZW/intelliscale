from django.db import models
from django.conf import settings

from .scale import Scale


class ScaleIdHistory(models.Model):
    """Logs changes to the scale_id of a Scale."""
    scale = models.ForeignKey(Scale, on_delete=models.CASCADE, related_name='id_history')
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    change_date = models.DateTimeField(auto_now_add=True)
    old_id = models.CharField(max_length=100)
    new_id = models.CharField(max_length=100)

    class Meta:
        ordering = ['-change_date']
