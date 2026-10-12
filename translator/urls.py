from django.urls import path
from . import views

urlpatterns = [
    path("", views.index, name="index"),
    path("history/", views.history, name="history"),
    path("job/<int:job_id>/", views.job_detail, name="job_detail"),
    path("api/translate/", views.api_translate, name="api_translate"),
]