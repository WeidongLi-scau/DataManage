import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


def original_upload_path(instance, filename):
    return f"originals/{instance.material_id}/{instance.version_number}/{uuid.uuid4().hex}"


def preview_upload_path(instance, filename):
    return f"previews/{instance.material_id}/{instance.version_number}/{uuid.uuid4().hex}.pdf"


class ArchiveGroup(models.Model):
    name = models.CharField(max_length=120, unique=True)
    description = models.CharField(max_length=500, blank=True)
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="archive_groups")
    is_public = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class MaterialCategory(models.Model):
    name = models.CharField(max_length=120, unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "资料分类"

    def __str__(self):
        return self.name


class Material(models.Model):
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=120, blank=True)
    tags = models.CharField(max_length=500, blank=True, help_text="多个标签用逗号分隔")
    groups = models.ManyToManyField(ArchiveGroup, related_name="materials")
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="uploaded_materials"
    )
    current_version = models.ForeignKey(
        "MaterialVersion", null=True, blank=True, on_delete=models.PROTECT, related_name="current_for"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="deleted_materials",
    )

    class Meta:
        ordering = ["-updated_at"]
        indexes = [models.Index(fields=["deleted_at", "updated_at"])]

    def __str__(self):
        return self.title

    @property
    def is_deleted(self):
        return self.deleted_at is not None

    @property
    def original_name(self):
        if self.current_version_id:
            return self.current_version.original_name
        return ""

    @property
    def extension(self):
        return self.current_version.extension if self.current_version_id else ""

    @property
    def processing_status(self):
        return self.current_version.status if self.current_version_id else "pending"


class MaterialVersion(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "排队中"
        PROCESSING = "processing", "处理中"
        READY = "ready", "已完成"
        FAILED = "failed", "解析失败"

    material = models.ForeignKey(Material, on_delete=models.CASCADE, related_name="versions")
    version_number = models.PositiveIntegerField()
    original_file = models.FileField(upload_to=original_upload_path)
    preview_file = models.FileField(upload_to=preview_upload_path, blank=True)
    original_name = models.CharField(max_length=255)
    extension = models.CharField(max_length=20, blank=True)
    content_type = models.CharField(max_length=160, blank=True)
    file_size = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)
    extracted_text = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    error_message = models.CharField(max_length=500, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version_number"]
        constraints = [
            models.UniqueConstraint(fields=["material", "version_number"], name="unique_material_version")
        ]

    def __str__(self):
        return f"{self.material.title} v{self.version_number}"


class AuditLog(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_events"
    )
    action = models.CharField(max_length=80)
    object_type = models.CharField(max_length=80, blank=True)
    object_id = models.CharField(max_length=80, blank=True)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]

