# users/serializers.py
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.hashers import make_password
from rest_framework import serializers

from .models import MasterKeyEnvelope

User = get_user_model()


def _from_hex(value, field, exact_len=None, min_len=None):
    try:
        decoded = bytes.fromhex(value)
    except Exception:
        raise serializers.ValidationError(f"Invalid hex for {field}")
    if exact_len is not None and len(decoded) != exact_len:
        raise serializers.ValidationError(f"{field} must be {exact_len} bytes")
    if min_len is not None and len(decoded) < min_len:
        raise serializers.ValidationError(f"{field} too short")
    return decoded


# ============================ REGISTER ============================
class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=12)

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
        return User.objects.create_user(
            username=email, email=email, password=validated_data["password"]
        )


# ========================= MASTER KEY META =======================
class MasterKeyMetaSerializer(serializers.ModelSerializer):
    encrypted_master_key_hex = serializers.SerializerMethodField()
    nonce_hex = serializers.SerializerMethodField()
    kdf_salt_hex = serializers.SerializerMethodField()

    class Meta:
        model = MasterKeyEnvelope
        fields = (
            "encrypted_master_key_hex", "nonce_hex", "kdf_salt_hex",
            "kdf_memory_kb", "kdf_iterations", "kdf_parallelism", "key_version",
        )

    def get_encrypted_master_key_hex(self, obj): return obj.enc_master_key.hex()
    def get_nonce_hex(self, obj): return obj.enc_master_key_nonce.hex()
    def get_kdf_salt_hex(self, obj): return obj.kdf_salt.hex()


# ====================== RECOVERY ENVELOPE META ===================
class RecoveryMetaSerializer(serializers.ModelSerializer):
    """Returned (by email) so the client can derive the Recovery-KEK and
    decrypt the master key during a logged-out reset."""
    recovery_encrypted_master_key_hex = serializers.SerializerMethodField()
    recovery_nonce_hex = serializers.SerializerMethodField()
    recovery_kdf_salt_hex = serializers.SerializerMethodField()

    class Meta:
        model = MasterKeyEnvelope
        fields = (
            "recovery_encrypted_master_key_hex", "recovery_nonce_hex",
            "recovery_kdf_salt_hex", "recovery_kdf_memory_kb",
            "recovery_kdf_iterations", "recovery_kdf_parallelism",
        )

    def get_recovery_encrypted_master_key_hex(self, obj): return obj.enc_master_key_recovery.hex()
    def get_recovery_nonce_hex(self, obj): return obj.enc_master_key_recovery_nonce.hex()
    def get_recovery_kdf_salt_hex(self, obj): return obj.recovery_kdf_salt.hex()


# ========================= MASTER KEY SETUP ======================
class MasterKeySetupSerializer(serializers.Serializer):
    # Password-wrapped envelope (required)
    enc_master_key = serializers.CharField()
    enc_master_key_nonce = serializers.CharField()
    kdf_salt = serializers.CharField()
    kdf_memory_kb = serializers.IntegerField()
    kdf_iterations = serializers.IntegerField()
    kdf_parallelism = serializers.IntegerField()

    # Recovery-wrapped envelope (optional, for backward-compat with old clients)
    enc_master_key_recovery = serializers.CharField(required=False)
    enc_master_key_recovery_nonce = serializers.CharField(required=False)
    recovery_kdf_salt = serializers.CharField(required=False)
    recovery_kdf_memory_kb = serializers.IntegerField(required=False)
    recovery_kdf_iterations = serializers.IntegerField(required=False)
    recovery_kdf_parallelism = serializers.IntegerField(required=False)
    recovery_auth_key = serializers.CharField(required=False)  # raw; stored hashed

    def validate_enc_master_key(self, v): return _from_hex(v, "enc_master_key", min_len=48)
    def validate_enc_master_key_nonce(self, v): return _from_hex(v, "nonce", exact_len=24)
    def validate_kdf_salt(self, v): return _from_hex(v, "kdf_salt", min_len=16)
    def validate_enc_master_key_recovery(self, v): return _from_hex(v, "enc_master_key_recovery", min_len=48)
    def validate_enc_master_key_recovery_nonce(self, v): return _from_hex(v, "recovery nonce", exact_len=24)
    def validate_recovery_kdf_salt(self, v): return _from_hex(v, "recovery_kdf_salt", min_len=16)

    def create(self, validated_data):
        user = self.context["request"].user
        recovery_auth_key = validated_data.pop("recovery_auth_key", None)
        if recovery_auth_key:
            validated_data["recovery_auth_hash"] = make_password(recovery_auth_key)
        return MasterKeyEnvelope.objects.create(user=user, **validated_data)


# ===== shared: a NEW password-wrapped envelope (for reset / change) =====
class _NewPasswordEnvelope(serializers.Serializer):
    new_password = serializers.CharField(min_length=12)
    enc_master_key = serializers.CharField()
    enc_master_key_nonce = serializers.CharField()
    kdf_salt = serializers.CharField()
    kdf_memory_kb = serializers.IntegerField()
    kdf_iterations = serializers.IntegerField()
    kdf_parallelism = serializers.IntegerField()

    def validate_new_password(self, v): validate_password(v); return v
    def validate_enc_master_key(self, v): return _from_hex(v, "enc_master_key", min_len=48)
    def validate_enc_master_key_nonce(self, v): return _from_hex(v, "nonce", exact_len=24)
    def validate_kdf_salt(self, v): return _from_hex(v, "kdf_salt", min_len=16)


class RecoverSerializer(_NewPasswordEnvelope):
    """Logged-out reset: prove the phrase via recovery_auth_key, set a new
    password, and upload the freshly password-wrapped envelope."""
    email = serializers.EmailField()
    recovery_auth_key = serializers.CharField()


class ChangePasswordSerializer(_NewPasswordEnvelope):
    """Logged-in change: client decrypts with old password, re-wraps with new."""
    pass
