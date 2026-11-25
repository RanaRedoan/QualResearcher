from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("upload/", views.upload, name="upload"),
    path("jobs/<uuid:public_id>/", views.job_detail, name="job_detail"),
    path("jobs/<uuid:public_id>/status/", views.job_status, name="job_status"),
    path("jobs/<uuid:public_id>/download/<str:kind>/", views.download, name="job_download"),
    path("jobs/<uuid:public_id>/translate/", views.translate_job, name="job_translate"),
    path("jobs/<uuid:public_id>/resume/", views.resume_job, name="job_resume"),
    path("jobs/<uuid:public_id>/delete/", views.delete_job, name="job_delete"),
]
