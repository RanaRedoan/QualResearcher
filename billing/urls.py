from django.urls import path

from . import views

urlpatterns = [
    path("", views.billing_overview, name="billing"),
    path("buy/", views.buy_credits, name="buy_credits"),
]
