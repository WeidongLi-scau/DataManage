from django.urls import path

from . import views


urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("password/change/", views.first_password_change, name="first_password_change"),
    path("", views.home, name="home"),
    path("upload/", views.upload, name="upload"),
    path("materials/<int:material_id>/", views.material_detail, name="material_detail"),
    path("materials/<int:material_id>/edit/", views.edit_material, name="edit_material"),
    path("materials/<int:material_id>/replace/", views.replace_material, name="replace_material"),
    path("materials/<int:material_id>/versions/<int:version_id>/retry/", views.retry_processing, name="retry_processing"),
    path("materials/<int:material_id>/download/", views.download_material, name="download_material"),
    path("materials/<int:material_id>/preview/", views.preview_material, name="preview_material"),
    path("materials/<int:material_id>/delete/", views.delete_material, name="delete_material"),
    path("materials/<int:material_id>/restore/", views.restore_material, name="restore_material"),
    path("recycle-bin/", views.recycle_bin, name="recycle_bin"),
    path("system/", views.system_status, name="system_status"),
    path("health/", views.health, name="health"),
]

