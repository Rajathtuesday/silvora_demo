# # users/serializers.py
# from rest_framework import serializers
# from django.contrib.auth.models import User
# from .models import UserProfile

# class RegisterSerializer(serializers.ModelSerializer):
#     password = serializers.CharField(write_only=True)

#     class Meta:
#         model = User
#         fields = ("username", "email", "password")

#     def create(self, validated_data):
#         user = User.objects.create_user(
#             username=validated_data["username"],
#             email=validated_data.get("email"),
#             password=validated_data["password"],
#         )
#         return user

# class MasterKeySerializer(serializers.ModelSerializer):
#     encrypted_master_key = serializers.SerializerMethodField(read_only=True)

#     class Meta:
#         model = UserProfile
#         fields = ("encrypted_master_key", "enc_algo", "key_salt_b64", "nonce_b64")

#     def get_encrypted_master_key(self, obj):
#         if obj.encrypted_master_key:
#             return obj.encrypted_master_key.hex()
#         return None



# # =-------------------------------------------------------------------=
# from django.contrib.auth import get_user_model
# from rest_framework import serializers

# User = get_user_model()

# class RegisterSerializer(serializers.ModelSerializer):
#     """
#     Only handles user creation. Master Key is initialized via signals,
#     but the encrypted value will be uploaded after login by the client.
#     """
#     password = serializers.CharField(write_only=True)

#     class Meta:
#         model = User
#         fields = ("id", "username", "email", "password")

#     def create(self, validated_data):
#         user = User.objects.create_user(
#             username=validated_data["username"],
#             email=validated_data.get("email"),
#             password=validated_data["password"],
#         )
#         return user




# from rest_framework import serializers
# from django.contrib.auth import get_user_model

# from .models import MasterKey

# User = get_user_model()



# class MasterKeyMetaSerializer(serializers.ModelSerializer):
#     """
#     What the client needs to know about stored master key.
#     We do NOT send the encrypted key here, just metadata.
#     """

#     has_master_key = serializers.SerializerMethodField(read_only=True)

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
#         return True


# class MasterKeySetupSerializer(serializers.ModelSerializer):
#     """
#     Used when client sends us the encrypted master key blob + KDF params.
#     """

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

#     def create(self, validated_data):
#         request = self.context.get("request")
#         user = request.user
#         return MasterKey.objects.create(user=user, **validated_data)

#     def update(self, instance, validated_data):
#         for attr, value in validated_data.items():
#             setattr(instance, attr, value)
#         instance.save()
#         return instance

# # =-------------------------------------------------------------------=


# # users/serializers.py
# from django.contrib.auth import get_user_model
# from rest_framework import serializers

# from .models import MasterKey

# User = get_user_model()


# # ============================
# # REGISTER
# # ============================

# class RegisterSerializer(serializers.ModelSerializer):
#     password = serializers.CharField(write_only=True, min_length=8)

#     class Meta:
#         model = User
#         fields = ("id", "username", "email", "password")

#     def validate_email(self, value):
#         if User.objects.filter(email=value).exists():
#             raise serializers.ValidationError("Email already registered")
#         return value

#     def create(self, validated_data):
#         user = User.objects.create_user(
#             username=validated_data["username"],
#             email=validated_data["email"],
#             password=validated_data["password"],
#         )
#         return user


# # ============================
# # MASTER KEY META
# # ============================

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


# # ============================
# # MASTER KEY SETUP
# # ============================

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
#         for attr, value in validated_data.items():
#             setattr(instance, attr, value)

#         instance.version += 1
#         instance.save()
#         return instance


# =======================================================================
# users/serializers.py
from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import MasterKey

User = get_user_model()


# ============================
# REGISTER (EMAIL-FIRST)
# ============================

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
        if len(value) < 8:
            raise serializers.ValidationError("Password must be at least 8 characters")
        if not any(c.islower() for c in value):
            raise serializers.ValidationError("Password must include a lowercase letter")
        if not any(c.isupper() for c in value):
            raise serializers.ValidationError("Password must include an uppercase letter")
        if not any(c.isdigit() for c in value):
            raise serializers.ValidationError("Password must include a number")
        return value

    def create(self, validated_data):
        email = validated_data["email"]

        # Use email as username internally (safe + compatible)
        user = User.objects.create_user(
            username=email,   # internal only
            email=email,
            password=validated_data["password"],
        )
        return user


# ============================
# MASTER KEY META
# ============================

class MasterKeyMetaSerializer(serializers.ModelSerializer):
    has_master_key = serializers.SerializerMethodField()

    class Meta:
        model = MasterKey
        fields = [
            "has_master_key",
            "kdf_algorithm",
            "kdf_iterations",
            "aead_algorithm",
            "version",
        ]

    def get_has_master_key(self, obj):
        return bool(obj.encrypted_master_key_hex)


# ============================
# MASTER KEY SETUP
# ============================

class MasterKeySetupSerializer(serializers.ModelSerializer):
    class Meta:
        model = MasterKey
        fields = [
            "encrypted_master_key_hex",
            "kdf_salt_b64",
            "kdf_algorithm",
            "kdf_iterations",
            "aead_algorithm",
            "nonce_b64",
        ]

    def update(self, instance, validated_data):
        for attr, value in validated_data.items():
            setattr(instance, attr, value)

        instance.version += 1
        instance.save()
        return instance
