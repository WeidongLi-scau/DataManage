from django.core.management.base import BaseCommand, CommandError

from library.search import _wait_for_task, ensure_index, update_index_settings


class Command(BaseCommand):
    help = "Create and configure the Meilisearch index."

    def handle(self, *args, **options):
        try:
            ensure_index()
            task_uid = update_index_settings()
            if task_uid:
                _wait_for_task(task_uid)
        except Exception as exc:
            raise CommandError(f"无法配置搜索索引：{exc}") from exc
        self.stdout.write(self.style.SUCCESS("搜索索引已配置。"))

