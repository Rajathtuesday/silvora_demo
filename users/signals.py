# users/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import User
from tenants.models import Tenant


@receiver(post_save, sender=User)
def create_individual_tenant_for_user(sender, instance, created, **kwargs):

    if created and not instance.is_superuser:

        # Create individual tenant
        tenant = Tenant.objects.create(
            name=f"{instance.username}-individual",
            tenant_type=Tenant.TYPE_INDIVIDUAL,
        )

        # Bind user to that tenant
        instance.tenant = tenant
        instance.save(update_fields=["tenant"])