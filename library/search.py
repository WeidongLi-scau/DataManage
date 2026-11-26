import logging

import requests
from django.conf import settings
from django.db.models import Q

from .models import ArchiveGroup, Material
from .permissions import accessible_materials, accessible_groups

logger = logging.getLogger(__name__)


def _headers():
    headers = {"Content-Type": "application/json"}
    if settings.MEILI_MASTER_KEY:
        headers["Authorization"] = f"Bearer {settings.MEILI_MASTER_KEY}"
    return headers


def index_version(version):
    if not settings.MEILI_ENABLED:
        return
    material = version.material
    if not Material.objects.filter(pk=material.pk, current_version_id=version.pk).exists():
        return
    document = search_document(version)
    url = f"{settings.MEILI_URL}/indexes/{settings.MEILI_INDEX}/documents?primaryKey=id"
    try:
        ensure_index()
        response = requests.put(url, json=[document], headers=_headers(), timeout=8)
        response.raise_for_status()
    except requests.RequestException:
        logger.exception("Could not update search index for material %s", material.pk)


def search_document(version):
    material = version.material
    document = {
        "id": material.pk,
        "title": material.title,
        "description": material.description,
        "tags": material.tags,
        "category_id": material.category_id,
        "category": material.category.name if material.category_id else "",
        "original_name": version.original_name,
        "extracted_text": version.extracted_text[:1_000_000],
        "group_ids": list(material.groups.values_list("id", flat=True)),
        "collection_ids": list(material.collections.values_list("id", flat=True)),
        "record_type": material.record_type,
        "document_date": material.document_date.isoformat() if material.document_date else "",
        "is_deleted": material.deleted_at is not None,
        "uploaded_at": int(material.created_at.timestamp()),
        "extension": version.extension,
    }
    if material.record_type == Material.RecordType.PUBLICATION:
        publication = getattr(material, "publication", None)
        if publication:
            document.update({
                "authors": publication.authors,
                "journal_name": publication.displayed_journal,
                "publication_year": publication.publication_year or (publication.publication_date.year if publication.publication_date else None),
                "volume": publication.volume,
                "issue": publication.issue,
                "pages": publication.pages,
                "doi": publication.doi,
                "bibtex_raw": publication.bibtex_raw,
            })
    return document


def update_index_settings():
    url = f"{settings.MEILI_URL}/indexes/{settings.MEILI_INDEX}/settings"
    try:
        response = requests.patch(
            url,
            json={
                "searchableAttributes": ["title", "original_name", "description", "tags", "category", "extracted_text", "authors", "journal_name", "volume", "issue", "pages", "doi", "bibtex_raw", "publication_year"],
                "filterableAttributes": ["group_ids", "collection_ids", "is_deleted", "extension", "category_id", "record_type", "document_date", "publication_year"],
                "sortableAttributes": ["uploaded_at", "document_date", "publication_year", "title"],
                "localizedAttributes": [{"locales": ["cmn"], "attributePatterns": ["title", "description", "tags", "category", "extracted_text", "authors", "journal_name"]}],
                "pagination": {"maxTotalHits": 5000},
            },
            headers=_headers(),
            timeout=8,
        )
        response.raise_for_status()
        return response.json().get("taskUid")
    except requests.RequestException:
        logger.exception("Could not update search settings")
        raise


def ensure_index():
    url = f"{settings.MEILI_URL}/indexes"
    try:
        response = requests.post(
            url,
            json={"uid": settings.MEILI_INDEX, "primaryKey": "id"},
            headers=_headers(),
            timeout=8,
        )
        if response.status_code == 409:
            return
        response.raise_for_status()
        task_uid = response.json().get("taskUid")
        if task_uid:
            _wait_for_task(task_uid)
    except requests.RequestException:
        logger.exception("Could not initialize search index")
        raise


def _wait_for_task(task_uid, timeout=60):
    import time

    deadline = time.monotonic() + timeout
    url = f"{settings.MEILI_URL}/tasks/{task_uid}"
    while time.monotonic() < deadline:
        response = requests.get(url, headers=_headers(), timeout=5)
        response.raise_for_status()
        task = response.json()
        if task.get("status") == "succeeded":
            return
        if task.get("status") == "failed":
            raise requests.HTTPError(f"Meilisearch task failed: {task.get('error')}")
        time.sleep(0.25)
    raise requests.Timeout("Timed out waiting for Meilisearch task")


def search_materials(user, query, limit=2500):
    allowed_ids = list(accessible_groups(user).values_list("id", flat=True))
    if user.is_staff or user.is_superuser:
        allowed_ids = list(ArchiveGroup.objects.values_list("id", flat=True))
    queryset = accessible_materials(user).filter(groups__in=allowed_ids).distinct()
    if not query:
        return queryset

    if allowed_ids and settings.MEILI_ENABLED:
        url = f"{settings.MEILI_URL}/indexes/{settings.MEILI_INDEX}/search"
        try:
            response = requests.post(
                url,
                json={
                    "q": query,
                    "limit": limit,
                    "filter": f"group_ids IN [{','.join(str(value) for value in allowed_ids)}] AND is_deleted = false",
                },
                headers=_headers(),
                timeout=3,
            )
            response.raise_for_status()
            ids = [int(hit["id"]) for hit in response.json().get("hits", []) if str(hit.get("id", "")).isdigit()]
            permitted = {item.pk: item for item in queryset.filter(pk__in=ids)}
            return [permitted[item_id] for item_id in ids if item_id in permitted]
        except requests.RequestException:
            logger.warning("Search engine unavailable; using database fallback", exc_info=True)

    return queryset.filter(
        Q(title__icontains=query)
        | Q(description__icontains=query)
        | Q(tags__icontains=query)
        | Q(category__name__icontains=query)
        | Q(current_version__original_name__icontains=query)
        | Q(current_version__extracted_text__icontains=query)
        | Q(publication__authors__icontains=query)
        | Q(publication__journal__name__icontains=query)
        | Q(publication__journal_name__icontains=query)
        | Q(publication__volume__icontains=query)
        | Q(publication__issue__icontains=query)
        | Q(publication__pages__icontains=query)
        | Q(publication__doi__icontains=query)
        | Q(publication__bibtex_raw__icontains=query)
        | (Q(publication__publication_year=int(query)) if query.isdigit() else Q(pk__in=[]))
    ).distinct()

