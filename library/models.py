import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


def original_upload_path(instance, filename):
    return f"originals/{instance.material_id}/{instance.version_number}/{uuid.uuid4().hex}"


def preview_upload_path(instance, filename):
    return f"previews/{instance.material_id}/{instance.version_number}/{uuid.uuid4().hex}.pdf"


def stored_file_path(instance, filename):
    return f"objects/{instance.sha256[:2]}/{instance.sha256}"


class ArchiveGroup(models.Model):
    name = models.CharField(max_length=120, unique=True)
    description = models.CharField(max_length=500, blank=True)
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="archive_groups")
    managers = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="managed_archive_groups")
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


class Journal(models.Model):
    name = models.CharField(max_length=255)
    normalized_name = models.CharField(max_length=255, unique=True)
    abbreviation = models.CharField(max_length=80, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def save(self, *args, **kwargs):
        self.normalized_name = " ".join(self.name.split()).casefold()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class MaterialCollection(models.Model):
    name = models.CharField(max_length=160)
    description = models.CharField(max_length=500, blank=True)
    groups = models.ManyToManyField(ArchiveGroup, related_name="collections")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_collections")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class UploadBatch(models.Model):
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="upload_batches")
    created_at = models.DateTimeField(auto_now_add=True)
    title = models.CharField(max_length=160, blank=True)

    class Meta:
        ordering = ["-created_at"]


class StoredFile(models.Model):
    sha256 = models.CharField(max_length=64, unique=True)
    file = models.FileField(upload_to=stored_file_path)
    file_size = models.PositiveBigIntegerField(default=0)
    content_type = models.CharField(max_length=160, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.sha256


class Folder(models.Model):
    group = models.ForeignKey(ArchiveGroup, on_delete=models.PROTECT, related_name="folders")
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="children")
    name = models.CharField(max_length=160)
    normalized_name = models.CharField(max_length=160)
    description = models.CharField(max_length=500, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deletion_batch = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["group", "parent", "normalized_name"], condition=models.Q(deleted_at__isnull=True, parent__isnull=False), name="unique_active_child_folder"),
            models.UniqueConstraint(fields=["group", "normalized_name"], condition=models.Q(deleted_at__isnull=True, parent__isnull=True), name="unique_active_root_folder"),
        ]

    def save(self, *args, **kwargs):
        self.normalized_name = self.name.strip().casefold()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Material(models.Model):
    class RecordType(models.TextChoices):
        MATERIAL = "material", "一般资料"
        PUBLICATION = "publication", "已发表成果"

    title = models.CharField(max_length=255)
    folder = models.ForeignKey(Folder, null=True, blank=True, on_delete=models.PROTECT, related_name="materials")
    folder_deletion_batch = models.UUIDField(null=True, blank=True)
    description = models.TextField(blank=True)
    category = models.ForeignKey(MaterialCategory, null=True, blank=True, on_delete=models.SET_NULL, related_name="materials")
    tags = models.CharField(max_length=500, blank=True, help_text="多个标签用逗号分隔")
    groups = models.ManyToManyField(ArchiveGroup, related_name="materials")
    collections = models.ManyToManyField(MaterialCollection, blank=True, related_name="materials")
    upload_batch = models.ForeignKey(UploadBatch, null=True, blank=True, on_delete=models.SET_NULL, related_name="materials")
    record_type = models.CharField(max_length=20, choices=RecordType.choices, default=RecordType.MATERIAL)
    document_date = models.DateField(null=True, blank=True)
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
    stored_file = models.ForeignKey(StoredFile, null=True, blank=True, on_delete=models.PROTECT, related_name="versions")
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


class UploadBatchItem(models.Model):
    batch = models.ForeignKey(UploadBatch, on_delete=models.CASCADE, related_name="items")
    material = models.OneToOneField(Material, null=True, blank=True, on_delete=models.SET_NULL, related_name="batch_item")
    original_name = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=MaterialVersion.Status.choices, default=MaterialVersion.Status.PENDING)
    error_message = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]


class PublicationMetadata(models.Model):
    material = models.OneToOneField(Material, on_delete=models.CASCADE, related_name="publication")
    bibtex_raw = models.TextField(blank=True)
    authors = models.TextField(blank=True)
    publication_date = models.DateField(null=True, blank=True)
    publication_year = models.PositiveSmallIntegerField(null=True, blank=True)
    publication_month = models.PositiveSmallIntegerField(null=True, blank=True)
    journal = models.ForeignKey(Journal, null=True, blank=True, on_delete=models.SET_NULL, related_name="publications")
    journal_name = models.CharField(max_length=255, blank=True)
    volume = models.CharField(max_length=80, blank=True)
    issue = models.CharField(max_length=80, blank=True)
    pages = models.CharField(max_length=120, blank=True)
    doi = models.CharField(max_length=255, blank=True)
    url = models.URLField(max_length=500, blank=True)

    @property
    def displayed_journal(self):
        return self.journal_name or (self.journal.name if self.journal_id else "")


class UserAccountState(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="account_state")
    must_change_password = models.BooleanField(default=True)


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

