# billing/urls.py
from django.urls import path

from .views import (
    PlayObfuscatedAccountIdView,
    PlayPurchaseVerifyView,
    PlayRTDNWebhookView,
    RazorpaySubscriptionWebhookView,
    WebBillingLinkView,
)

urlpatterns = [
    path("web-link/", WebBillingLinkView.as_view()),
    path("webhook/", RazorpaySubscriptionWebhookView.as_view()),
    path("play/account-id/", PlayObfuscatedAccountIdView.as_view()),
    path("play/verify/", PlayPurchaseVerifyView.as_view()),
    path("play/rtdn/", PlayRTDNWebhookView.as_view()),
]
