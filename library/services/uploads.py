import hashlib
from pathlib import Path

from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction

from library.models import MaterialVersion, StoredFile


def checksum(uploaded_file):
    digest = hashlib.sha256()
    for chunk in uploaded_file.chunks():
        digest.update(chunk)
    uploaded_file.seek(0)
    return digest.hexdigest()


def create_version(*, material, uploaded_file, user, version_number):
    sha256 = checksum(uploaded_file)
    stored_file = StoredFile.objects.filter(sha256=sha256).first()
    if stored_file is None:
        uploaded_file.seek(0)
        candidate = StoredFile(
            sha256=sha256,
            file_size=uploaded_file.size,
            content_type=uploaded_file.content_type or "application/octet-stream",
        )
        candidate.file.save(sha256, uploaded_file, save=False)
        try:
            with transaction.atomic():
                candidate.save()
            stored_file = candidate
        except IntegrityError:
            stored_file = StoredFile.objects.get(sha256=sha256)
            if candidate.file.name != stored_file.file.name:
                default_storage.delete(candidate.file.name)

    original_name = Path(uploaded_file.name).name[:255]
    extension = Path(original_name).suffix.lower()[:20]
    return MaterialVersion.objects.create(
        material=material,
        version_number=version_number,
        original_file=stored_file.file.name,
        stored_file=stored_file,
        original_name=original_name,
        extension=extension,
        content_type=uploaded_file.content_type or "application/octet-stream",
        file_size=uploaded_file.size,
        sha256=sha256,
        created_by=user,
    )
