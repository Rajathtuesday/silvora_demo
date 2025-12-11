# users/admin.py
# from django.contrib import admin
# from .models import MasterKey


# @admin.register(MasterKey)
# class MasterKeyAdmin(admin.ModelAdmin):
#     list_display = ("user", "version", "created_at", "updated_at")
#     search_fields = ("user__username",)



# users/admin.py
from django.contrib import admin
from .models import MasterKey


@admin.register(MasterKey)
class MasterKeyAdmin(admin.ModelAdmin):
    list_display = ("user", "version", "kdf_algorithm", "aead_algorithm", "created_at")
    search_fields = ("user__username",)
