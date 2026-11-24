from getpass import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError

from library.models import ArchiveGroup


class Command(BaseCommand):
    help = "Create the initial admin account and public archive group."

    def handle(self, *args, **options):
        user_model = get_user_model()
        user = user_model.objects.filter(username="admin").first()
        if user and user.is_superuser:
            self.stdout.write(self.style.WARNING("admin 管理员已存在，未作修改。"))
        else:
            if user:
                raise CommandError("用户名 admin 已被普通账户占用，请先在管理后台处理。")
            password = getpass("设置 admin 密码：")
            confirmation = getpass("再次输入密码：")
            if not password or password != confirmation:
                raise CommandError("密码为空或两次输入不一致。")
            candidate = user_model(username="admin")
            try:
                validate_password(password, user=candidate)
            except ValidationError as exc:
                raise CommandError("密码不符合要求：" + "；".join(exc.messages)) from exc
            user = user_model.objects.create_superuser(username="admin", email="", password=password)
            self.stdout.write(self.style.SUCCESS("管理员账户 admin 已创建。"))
        public_group, created = ArchiveGroup.objects.get_or_create(
            is_public=True,
            defaults={"name": "公共资料", "description": "全体启用成员可访问的共享资料"},
        )
        if not public_group.is_active:
            public_group.is_active = True
            public_group.save(update_fields=["is_active"])
        if created:
            self.stdout.write(self.style.SUCCESS("公共资料分组已创建。"))

