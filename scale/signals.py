from django.db.models.signals import pre_save
from django.dispatch import receiver
from .models import Scale
import uuid

@receiver(pre_save, sender=Scale)
def assign_scale_id(sender, instance, **kwargs):
    if not instance.pk and not instance.scale_id:
        # Loop to ensure the generated ID is unique, though collision is highly unlikely
        while True:
            new_id = f"SCL-{uuid.uuid4().hex[:6].upper()}"
            if not Scale.objects.filter(scale_id=new_id).exists():
                instance.scale_id = new_id
                break
