from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import MasterKey

User = get_user_model()

@receiver(post_save, sender=User)
def create_master_key_record(sender, instance, created, **kwargs):
    """
    When a new user registers, create an empty Master Key record.
    The actual encrypted key will be uploaded later by the client.
    """
    if created:
        MasterKey.objects.create(user=instance)
