from django.urls import path

from . import views

urlpatterns = [
    path("setup/", views.setup, name="desktop_setup"),
    path("test-key/", views.test_key, name="desktop_test_key"),
    path("settings/", views.app_settings, name="desktop_settings"),
    path("keys/add/", views.key_add, name="desktop_key_add"),
    path("keys/<str:key_id>/", views.key_action, name="desktop_key_action"),
    path("save/<uuid:public_id>/<str:kind>/", views.save_word, name="desktop_save"),
    path("reveal/", views.reveal, name="desktop_reveal"),
    path("jobs.json", views.jobs_json, name="desktop_jobs"),
    path("update-check/", views.update_check, name="desktop_update"),
    path("help/gemini-key/", views.help_gemini_key, name="desktop_help_key"),
    path("notifications/", views.notifications, name="desktop_notifications"),
    path("notifications/read/", views.notifications_read, name="desktop_notifications_read"),
    path("ping/", views.ping, name="desktop_ping"),
]
