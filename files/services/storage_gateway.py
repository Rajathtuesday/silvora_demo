import os
from django.conf import settings

if getattr(settings, 'R2_ACCOUNT_ID', None):
    from .r2_storage_adapter import R2StorageGateway as StorageGateway
else:
    from .local_storage_gateway import StorageGateway as LocalStorageGateway
    StorageGateway = LocalStorageGateway
