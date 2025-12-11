# # Create your models here.
# # users/models.py
# from django.db import models
# from django.conf import settings

# class UserProfile(models.Model):
#     user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
#     encrypted_master_key = models.BinaryField(null=True, blank=True)
#     enc_algo = models.CharField(max_length=64, default="XCHACHA20_POLY1305")
#     key_salt_b64 = models.CharField(max_length=256, null=True, blank=True)
#     nonce_b64 = models.CharField(max_length=256, null=True, blank=True)
#     created_at = models.DateTimeField(auto_now_add=True)
#     updated_at = models.DateTimeField(auto_now=True)

#     def __str__(self):
#         return f"profile:{self.user.username}"




# # class UserMasterKey(models.Model):
# #     """
# #     Stores the encrypted master key for a user.

# #     IMPORTANT:
# #     - This is NEVER the plaintext key.
# #     - Everything here comes from the client-side envelope.
# #     """

# #     user = models.OneToOneField(
# #         settings.AUTH_USER_MODEL,
# #         on_delete=models.CASCADE,
# #         related_name="master_key",
# #     )

# #     mk_ciphertext_b64 = models.TextField()
# #     mk_nonce_b64 = models.CharField(max_length=128)
# #     mk_mac_b64 = models.CharField(max_length=128)
# #     mk_salt_b64 = models.CharField(max_length=128)
# #     mk_algo = models.CharField(
# #         max_length=128,
# #         default="xchacha20-poly1305+pbkdf2-sha256",
# #     )

# #     created_at = models.DateTimeField(auto_now_add=True)
# #     updated_at = models.DateTimeField(auto_now=True)

# #     def __str__(self):
# #         return f"UserMasterKey(user={self.user_id}, algo={self.mk_algo})"




# # users/models.py
# from django.conf import settings
# from django.db import models


# class MasterKey(models.Model):
#     """
#     Per-user master key record.
#     We NEVER store the plaintext master key, only an AEAD-encrypted blob
#     plus KDF parameters so the client can derive the same key from the login password.
#     """

#     user = models.OneToOneField(
#         settings.AUTH_USER_MODEL,
#         on_delete=models.CASCADE,
#         related_name="master_key",
#     )

#     # Hex-encoded ciphertext of the 32-byte master key
#     encrypted_master_key_hex = models.TextField()

#     # Base64 salt for PBKDF2 / Argon2
#     kdf_salt_b64 = models.CharField(max_length=255)

#     # How the client derived the KEK (Key Encryption Key)
#     kdf_algorithm = models.CharField(
#         max_length=32,
#         default="pbkdf2-hmac-sha256",
#     )
#     kdf_iterations = models.IntegerField(default=150_000)

#     # AEAD algorithm metadata (for future migration)
#     aead_algorithm = models.CharField(
#         max_length=32,
#         default="xchacha20-poly1305",
#     )
#     nonce_b64 = models.CharField(max_length=255)

#     # Versioning for future rotations / format changes
#     version = models.IntegerField(default=1)

#     created_at = models.DateTimeField(auto_now_add=True)
#     updated_at = models.DateTimeField(auto_now=True)

#     # Reserved for future recovery mechanism (Hybrid C) – KEEP NULL FOR NOW
#     recovery_blob = models.TextField(
#         null=True,
#         blank=True,
#         help_text="Reserved for future encrypted recovery bundle.",
#     )

#     def __str__(self):
#         return f"MasterKey(user={self.user_id}, version={self.version})"


# =-----------------------------------------------------------------------=


# # users/models.py
# from django.db import models
# from django.contrib.auth.models import User


# class UserMasterKey(models.Model):
#     """
#     Stores the encrypted master key and all metadata
#     required for the client to decrypt it locally.
#     Zero plaintext key stored server-side.
#     """
#     user = models.OneToOneField(
#         User,
#         on_delete=models.CASCADE,
#         related_name="e2ee_master_key",
#     )

#     enc_master_key_b64 = models.TextField()   # ciphertext + mac (base64)
#     enc_nonce_b64 = models.CharField(max_length=64)  # XChaCha20 nonce (base64)
#     kdf_salt_b64 = models.CharField(max_length=64)

#     # metadata for future upgrades
#     kdf_algo = models.CharField(max_length=64, default="pbkdf2-hmac-sha256-600k")
#     enc_algo = models.CharField(max_length=64, default="xchacha20-poly1305")
#     version = models.IntegerField(default=1)

#     # reserved for future account recovery
#     backup_enc_master_key_b64 = models.TextField(null=True, blank=True)
#     backup_nonce_b64 = models.CharField(max_length=64, null=True, blank=True)
#     backup_kdf_salt_b64 = models.CharField(max_length=64, null=True, blank=True)
#     backup_kdf_algo = models.CharField(max_length=64, default="pbkdf2-hmac-sha256-600k")

#     created_at = models.DateTimeField(auto_now_add=True)
#     updated_at = models.DateTimeField(auto_now=True)

#     def __str__(self):
#         return f"UserMasterKey(user={self.user_id})"

# ==---------------------------------------------------------------------==


# from django.conf import settings
# from django.db import models


# class MasterKey(models.Model):
#     """
#     Per-user encrypted master key metadata.
#     NEVER store plaintext master keys.
#     """

#     user = models.OneToOneField(
#         settings.AUTH_USER_MODEL,
#         on_delete=models.CASCADE,
#         related_name="master_key",
#     )

#     encrypted_master_key_hex = models.TextField()

#     kdf_salt_b64 = models.CharField(max_length=255)
#     kdf_algorithm = models.CharField(max_length=32, default="pbkdf2-hmac-sha256")
#     kdf_iterations = models.IntegerField(default=150_000)

#     aead_algorithm = models.CharField(
#         max_length=32, default="xchacha20-poly1305"
#     )
#     nonce_b64 = models.CharField(max_length=255)

#     version = models.IntegerField(default=1)

#     recovery_blob = models.TextField(
#         null=True, blank=True,
#         help_text="Reserved for future recovery bundle."
#     )

#     created_at = models.DateTimeField(auto_now_add=True)
#     updated_at = models.DateTimeField(auto_now=True)

#     def __str__(self):
#         return f"MasterKey(user={self.user_id}, v={self.version})"




# ============---------------------------------------------------============
# from django.conf import settings
# from django.db import models

# class MasterKey(models.Model):
#     user = models.OneToOneField(
#         settings.AUTH_USER_MODEL,
#         on_delete=models.CASCADE,
#         related_name="master_key",
#     )
#     encrypted_master_key_hex = models.TextField()
#     kdf_salt_b64 = models.CharField(max_length=255)
#     kdf_algorithm = models.CharField(max_length=32,
#                                     default="pbkdf2-hmac-sha256")
#     kdf_iterations = models.IntegerField(default=150_000)
#     aead_algorithm = models.CharField(max_length=32,
#                                     default="xchacha20-poly1305")
#     nonce_b64 = models.CharField(max_length=255)
#     version = models.IntegerField(default=1)
#     created_at = models.DateTimeField(auto_now_add=True)
#     updated_at = models.DateTimeField(auto_now=True)
#     recovery_blob = models.TextField(null=True, blank=True)

#     def __str__(self):
#         return f"MasterKey(user={self.user_id}, version={self.version})"
# ===--------------------------------------------------------------------------===
# users/models.py
from django.conf import settings
from django.db import models


class MasterKey(models.Model):
    """
    Per-user master key record.

    We NEVER store the plaintext master key, only an AEAD-encrypted blob
    plus KDF parameters so the client can derive the same KEK from the password.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="master_key",
    )

    # Hex-encoded ciphertext of the 32-byte master key (encrypted with KEK)
    encrypted_master_key_hex = models.TextField()

    # Base64 salt for PBKDF2 / Argon2 on the client
    kdf_salt_b64 = models.CharField(max_length=255)

    # How the client derived the KEK (Key Encryption Key)
    kdf_algorithm = models.CharField(
        max_length=32,
        default="pbkdf2-hmac-sha256",
    )
    kdf_iterations = models.IntegerField(default=150_000)

    # AEAD algorithm metadata (for future migration)
    aead_algorithm = models.CharField(
        max_length=32,
        default="xchacha20-poly1305",
    )
    nonce_b64 = models.CharField(max_length=255)

    # Versioning for future rotations / format changes
    version = models.IntegerField(default=1)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Reserved for future recovery mechanism – leave NULL for now
    recovery_blob = models.TextField(
        null=True,
        blank=True,
        help_text="Reserved for future encrypted recovery bundle.",
    )

    def __str__(self):
        return f"MasterKey(user={self.user_id}, version={self.version})"
