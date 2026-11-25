from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "QualResearcher admin"
admin.site.site_title = "QualResearcher admin"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("billing/", include("billing.urls")),
    path("app/", include("transcription.urls")),
    path("desktop/", include("desktop.urls")),
    path("", include("pages.urls")),
]
