from django.urls import path

from .views import LivenessView, ReadinessView

urlpatterns = [
    path("health/live/", LivenessView.as_view(), name="health-live"),
    path("health/ready/", ReadinessView.as_view(), name="health-ready"),
]
