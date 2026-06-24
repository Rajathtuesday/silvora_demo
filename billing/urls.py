# billing/urls.py
from django.urls import path

from .views import CreateSubscriptionView, RazorpaySubscriptionWebhookView

urlpatterns = [
    path("subscribe/", CreateSubscriptionView.as_view()),
    path("webhook/", RazorpaySubscriptionWebhookView.as_view()),
]
