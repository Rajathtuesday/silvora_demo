# billing/serializers.py
from rest_framework import serializers

from .models import RazorpayPlan


class CreateSubscriptionSerializer(serializers.Serializer):
    tier = serializers.ChoiceField(choices=["pro", "enterprise"])
    interval = serializers.ChoiceField(choices=RazorpayPlan.Interval.values)


class VerifyPlayPurchaseSerializer(serializers.Serializer):
    purchase_token = serializers.CharField(max_length=500)
    product_id = serializers.CharField(max_length=100)
