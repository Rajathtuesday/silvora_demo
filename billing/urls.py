# billing/urls.py
from django.urls import path

from .views import RazorpaySubscriptionWebhookView, WebBillingLinkView

urlpatterns = [
    path("web-link/", WebBillingLinkView.as_view()),
    path("webhook/", RazorpaySubscriptionWebhookView.as_view()),
]
