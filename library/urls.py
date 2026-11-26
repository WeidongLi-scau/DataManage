from django.urls import path

from . import views
from . import management_views
from . import workflow_views


urlpatterns = [
    path("account/", workflow_views.personal_settings, name="personal_settings"),
    path("projects/", workflow_views.projects, name="projects"),
    path("projects/<int:group_id>/", workflow_views.projects, name="project_detail"),
    path("my/uploads/", workflow_views.my_upload_batches, name="my_upload_batches"),
    path("materials/bulk/", workflow_views.bulk_materials, name="bulk_materials"),
    path("publications/", workflow_views.publications, name="publications"),
    path("manage/users/<int:user_id>/reset-password/", workflow_views.reset_member_password, name="reset_member_password"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("password/change/", views.first_password_change, name="first_password_change"),
    path("", views.home, name="home"),
    path("upload/", views.upload, name="upload"),
    path("batches/<int:batch_id>/", views.upload_batch_detail, name="upload_batch_detail"),
    path("materials/<int:material_id>/", views.material_detail, name="material_detail"),
    path("materials/<int:material_id>/edit/", views.edit_material, name="edit_material"),
    path("materials/<int:material_id>/replace/", views.replace_material, name="replace_material"),
    path("materials/<int:material_id>/versions/<int:version_id>/retry/", views.retry_processing, name="retry_processing"),
    path("materials/<int:material_id>/versions/<int:version_id>/download/", views.download_material, name="version_download"),
    path("materials/<int:material_id>/versions/<int:version_id>/preview/", views.preview_material, name="version_preview"),
    path("materials/<int:material_id>/versions/<int:version_id>/preview/content/", views.preview_content, name="version_preview_content"),
    path("materials/<int:material_id>/versions/<int:version_id>/restore/", workflow_views.restore_version, name="restore_version"),
    path("materials/<int:material_id>/download/", views.download_material, name="download_material"),
    path("materials/<int:material_id>/preview/", views.preview_material, name="preview_material"),
    path("materials/<int:material_id>/preview/content/", views.preview_content, name="preview_content"),
    path("materials/<int:material_id>/delete/", views.delete_material, name="delete_material"),
    path("materials/<int:material_id>/restore/", views.restore_material, name="restore_material"),
    path("recycle-bin/", views.recycle_bin, name="recycle_bin"),
    path("system/", views.system_status, name="system_status"),
    path("bibliography/parse/", views.parse_bibtex, name="parse_bibtex"),
    path("manage/", management_views.management_home, name="management_home"),
    path("manage/users/", management_views.manage_users, name="manage_users"),
    path("manage/users/<int:user_id>/", management_views.manage_users, name="edit_managed_user"),
    path("manage/groups/", management_views.manage_groups, name="manage_groups"),
    path("manage/groups/<int:group_id>/", management_views.manage_groups, name="edit_managed_group"),
    path("manage/collections/", management_views.manage_collections, name="manage_collections"),
    path("manage/collections/<int:collection_id>/", management_views.manage_collections, name="edit_collection"),
    path("manage/categories/", management_views.manage_categories, name="manage_categories"),
    path("manage/categories/<int:category_id>/", management_views.manage_categories, name="edit_category"),
    path("manage/journals/", management_views.manage_journals, name="manage_journals"),
    path("manage/journals/<int:journal_id>/", management_views.manage_journals, name="edit_journal"),
    path("health/", views.health, name="health"),
]

