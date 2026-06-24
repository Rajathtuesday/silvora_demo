# billing/serializers.py
from rest_framework import serializers

from .models import RazorpayPlan


class CreateSubscriptionSerializer(serializers.Serializer):
    tier = serializers.ChoiceField(choices=["pro", "enterprise"])
    interval = serializers.ChoiceField(choices=RazorpayPlan.Interval.values)
