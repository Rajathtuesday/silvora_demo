# feedback/urls.py
from django.urls import path

from .views import beta_feedback

urlpatterns = [
    path("", beta_feedback, name="beta_feedback"),
]
