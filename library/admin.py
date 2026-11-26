from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import ArchiveGroup, AuditLog, Journal, MaterialCategory, MaterialCollection, MaterialVersion, PublicationMetadata, StoredFile, UploadBatch


@admin.register(ArchiveGroup)
class ArchiveGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "is_public", "is_active", "member_count", "created_at")
    list_filter = ("is_public", "is_active")
    search_fields = ("name", "description", "members__username")
    filter_horizontal = ("members", "managers")

    def get_fields(self, request, obj=None):
        fields = ["name", "description", "members", "is_active"]
        if obj and obj.is_public:
            fields.append("is_public")
        return fields

    def get_readonly_fields(self, request, obj=None):
        return ("is_public",) if obj and obj.is_public else ()

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        AuditLog.objects.create(
            actor=request.user,
            action="group.update" if change else "group.create",
            object_type="ArchiveGroup",
            object_id=str(obj.pk),
            details={"name": obj.name, "is_active": obj.is_active},
        )

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        AuditLog.objects.create(
            actor=request.user,
            action="group.members.update",
            object_type="ArchiveGroup",
            object_id=str(form.instance.pk),
            details={"member_ids": list(form.instance.members.values_list("id", flat=True))},
        )

    @admin.display(description="成员数")
    def member_count(self, obj):
        return obj.members.count()


@admin.register(MaterialCategory)
class MaterialCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name",)

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        AuditLog.objects.create(
            actor=request.user,
            action="category.update" if change else "category.create",
            object_type="MaterialCategory",
            object_id=str(obj.pk),
            details={"name": obj.name, "is_active": obj.is_active},
        )


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "action", "object_type", "object_id")
    list_filter = ("action", "object_type")
    search_fields = ("actor__username", "object_id", "action")
    readonly_fields = ("actor", "action", "object_type", "object_id", "details", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ManagedUserAdmin(DjangoUserAdmin):
    list_display = ("username", "first_name", "last_name", "is_active", "is_staff", "last_login")
    list_filter = ("is_active", "is_staff")

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        AuditLog.objects.create(
            actor=request.user,
            action="account.update" if change else "account.create",
            object_type="User",
            object_id=str(obj.pk),
            details={"username": obj.username, "is_active": obj.is_active, "is_staff": obj.is_staff},
        )


admin.site.unregister(get_user_model())
admin.site.register(get_user_model(), ManagedUserAdmin)


@admin.register(MaterialCollection)
class MaterialCollectionAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "created_by", "created_at")
    list_filter = ("is_active", "groups")
    search_fields = ("name", "description")
    filter_horizontal = ("groups",)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Journal)
class JournalAdmin(admin.ModelAdmin):
    list_display = ("name", "abbreviation", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "abbreviation")

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PublicationMetadata)
class PublicationMetadataAdmin(admin.ModelAdmin):
    list_display = ("material", "displayed_journal", "publication_year", "doi")
    search_fields = ("material__title", "authors", "journal_name", "journal__name", "doi")
    readonly_fields = ("material",)

    @admin.display(description="期刊 / 来源")
    def displayed_journal(self, obj):
        return obj.displayed_journal


@admin.register(UploadBatch)
class UploadBatchAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "created_by", "created_at")
    readonly_fields = ("created_by", "created_at")

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StoredFile)
class StoredFileAdmin(admin.ModelAdmin):
    list_display = ("sha256", "file_size", "content_type", "created_at")
    search_fields = ("sha256",)
    readonly_fields = ("sha256", "file", "file_size", "content_type", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

