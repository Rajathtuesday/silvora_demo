# # users/serializers.py

# from django.contrib.auth import get_user_model
# from django.contrib.auth.password_validation import validate_password
# from rest_framework import serializers

# from tenants.models import Tenant
# from .models import MasterKey

# User = get_user_model()


# # ============================================================
# # REGISTER (EMAIL-FIRST + AUTO TENANT CREATION)
# # ============================================================

# class RegisterSerializer(serializers.ModelSerializer):
#     password = serializers.CharField(write_only=True, min_length=8)

#     class Meta:
#         model = User
#         fields = ("id", "email", "password")

#     def validate_email(self, value):
#         email = value.strip().lower()
#         if User.objects.filter(email=email).exists():
#             raise serializers.ValidationError("Email already registered")
#         return email

#     def validate_password(self, value):
#         validate_password(value)
#         return value

#     def create(self, validated_data):
#         email = validated_data["email"]

#         # 1️⃣ Create personal tenant
#         tenant = Tenant.objects.create(
#             name=email,
#             tenant_type=Tenant.TYPE_INDIVIDUAL,
#         )

#         # 2️⃣ Create user
#         user = User.objects.create_user(
#             username=email,
#             email=email,
#             password=validated_data["password"],
#             tenant=tenant,
#         )

#         # 3️⃣ Create empty MasterKey record (required for zero-knowledge flow)
#         MasterKey.objects.create(user=user)

#         return user


# # ============================================================
# # MASTER KEY META
# # ============================================================

# class MasterKeyMetaSerializer(serializers.ModelSerializer):
#     has_master_key = serializers.SerializerMethodField()

#     class Meta:
#         model = MasterKey
#         fields = [
#             "has_master_key",
#             "kdf_algorithm",
#             "kdf_iterations",
#             "aead_algorithm",
#             "version",
#         ]

#     def get_has_master_key(self, obj):
#         return bool(obj.encrypted_master_key_hex)


# # ============================================================
# # MASTER KEY SETUP / ROTATION
# # ============================================================

# class MasterKeySetupSerializer(serializers.ModelSerializer):

#     class Meta:
#         model = MasterKey
#         fields = [
#             "encrypted_master_key_hex",
#             "kdf_salt_b64",
#             "kdf_algorithm",
#             "kdf_iterations",
#             "aead_algorithm",
#             "nonce_b64",
#         ]

#     def update(self, instance, validated_data):
#         for attr, value in validated_data.items():``
#             setattr(instance, attr, value)

#         instance.version += 1
#         instance.save()
#         return instance




# ===========================v2================== lets give a try as they are saying masterkey will be created else where ====================
# users/serializers.py
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from tenants.models import Tenant
from .models import MasterKeyEnvelope
import base64

User = get_user_model()


# =============================
# REGISTER
# =============================

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ("id", "email", "password")

    def validate_email(self, value):
        email = value.strip().lower()
        if User.objects.filter(email=email).exists():
            raise serializers.ValidationError("Email already registered")
        return email

    def validate_password(self, value):
        validate_password(value)
        return value

    def create(self, validated_data):
        email = validated_data["email"]

        tenant = Tenant.objects.create(
            name=email,
            tenant_type=Tenant.TYPE_INDIVIDUAL,
        )

        user = User.objects.create_user(
            username=email,
            email=email,
            password=validated_data["password"],
            tenant=tenant,
        )

        return user


# =============================
# MASTER KEY META
# =============================



class MasterKeyMetaSerializer(serializers.ModelSerializer):

    encrypted_master_key_hex = serializers.SerializerMethodField()
    nonce_hex = serializers.SerializerMethodField()
    kdf_salt_hex = serializers.SerializerMethodField()

    class Meta:
        model = MasterKeyEnvelope
        fields = (
            "encrypted_master_key_hex",
            "nonce_hex",
            "kdf_salt_hex",
            "kdf_memory_kb",
            "kdf_iterations",
            "kdf_parallelism",
            "key_version",
        )

    def get_encrypted_master_key_hex(self, obj):
        return obj.enc_master_key.hex()

    def get_nonce_hex(self, obj):
        return obj.enc_master_key_nonce.hex()

    def get_kdf_salt_hex(self, obj):
        return obj.kdf_salt.hex()


# =============================
# MASTER KEY SETUP
# =============================


# =============================
# MASTER KEY SETUP
# =============================
class MasterKeySetupSerializer(serializers.ModelSerializer):

    # 🔴 Override BinaryFields to accept hex strings
    enc_master_key = serializers.CharField()
    enc_master_key_nonce = serializers.CharField()
    kdf_salt = serializers.CharField()

    class Meta:
        model = MasterKeyEnvelope
        fields = (
            "enc_master_key",
            "enc_master_key_nonce",
            "kdf_salt",
            "kdf_memory_kb",
            "kdf_iterations",
            "kdf_parallelism",
        )

    def validate_enc_master_key(self, value):
        try:
            decoded = bytes.fromhex(value)
        except Exception:
            raise serializers.ValidationError("Invalid hex for enc_master_key")

        if len(decoded) < 48:
            raise serializers.ValidationError("Encrypted master key too short")

        return decoded

    def validate_enc_master_key_nonce(self, value):
        try:
            decoded = bytes.fromhex(value)
        except Exception:
            raise serializers.ValidationError("Invalid hex for nonce")

        if len(decoded) != 24:
            raise serializers.ValidationError("Nonce must be 24 bytes")

        return decoded

    def validate_kdf_salt(self, value):
        try:
            decoded = bytes.fromhex(value)
        except Exception:
            raise serializers.ValidationError("Invalid hex for kdf_salt")

        if len(decoded) < 16:
            raise serializers.ValidationError("Salt too short")

        return decoded

    def create(self, validated_data):
        user = self.context["request"].user
        return MasterKeyEnvelope.objects.create(
            user=user,
            **validated_data,
        )