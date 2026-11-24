import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from library.models import Material
from library.search import _headers, _wait_for_task, ensure_index, search_document, update_index_settings


class Command(BaseCommand):
    help = "Rebuild the Meilisearch index from PostgreSQL metadata and extracted text."

    def handle(self, *args, **options):
        try:
            ensure_index()
            settings_task = update_index_settings()
            if settings_task:
                _wait_for_task(settings_task)
            response = requests.delete(
                f"{settings.MEILI_URL}/indexes/{settings.MEILI_INDEX}/documents",
                headers=_headers(),
                timeout=10,
            )
            response.raise_for_status()
            delete_task = response.json().get("taskUid")
            if delete_task:
                _wait_for_task(delete_task)
            versions = (
                Material.objects.select_related("current_version")
                .prefetch_related("groups")
                .exclude(current_version__isnull=True)
                .order_by("id")
            )
            batch = []
            count = 0
            for material in versions.iterator(chunk_size=50):
                batch.append(search_document(material.current_version))
                if len(batch) == 50:
                    count += self._send_batch(batch)
                    batch = []
            if batch:
                count += self._send_batch(batch)
        except requests.RequestException as exc:
            raise CommandError(f"索引重建失败：{exc}") from exc
        self.stdout.write(self.style.SUCCESS(f"已将 {count} 条资料提交至搜索索引。"))

    def _send_batch(self, documents):
        response = requests.put(
            f"{settings.MEILI_URL}/indexes/{settings.MEILI_INDEX}/documents?primaryKey=id",
            json=documents,
            headers=_headers(),
            timeout=60,
        )
        response.raise_for_status()
        task_uid = response.json().get("taskUid")
        if task_uid:
            _wait_for_task(task_uid)
        return len(documents)

