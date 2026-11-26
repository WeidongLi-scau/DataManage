import unicodedata

from django.core.exceptions import ValidationError
from django.db import transaction

from library.models import ArchiveGroup, Folder
from library.permissions import accessible_groups


def clean_name(value):
    value = unicodedata.normalize("NFC", value).strip()
    if not value or value in {".", ".."} or len(value) > 160 or any(char in value for char in '/\\\x00') or any(ord(char) < 32 for char in value):
        raise ValidationError("名称须为 1–160 个字符，不能包含斜杠、控制字符或使用 .、..。")
    return value


def visible_folders(user):
    return Folder.objects.filter(group__in=accessible_groups(user), deleted_at__isnull=True).select_related("group", "parent")


def folder_choices(user, group=None):
    folders = list(visible_folders(user).filter(group=group) if group else visible_folders(user))
    by_id = {folder.pk: folder for folder in folders}
    for folder in folders:
        names, seen, current = [], set(), folder
        while current and current.pk not in seen:
            seen.add(current.pk)
            names.append(current.name)
            current = by_id.get(current.parent_id)
        folder.display_path = " / ".join(reversed(names))
    return sorted(folders, key=lambda folder: (folder.group_id, folder.display_path.casefold()))


def can_create(user, group):
    return user.is_staff or user.is_superuser or group.members.filter(pk=user.pk).exists() or group.managers.filter(pk=user.pk).exists()


def can_manage_folder(user, folder):
    return user.is_staff or user.is_superuser or folder.created_by_id == user.pk or folder.group.managers.filter(pk=user.pk).exists()


def descendants(folder, include_deleted=False):
    query = Folder.objects.filter(group_id=folder.group_id)
    if not include_deleted:
        query = query.filter(deleted_at__isnull=True)
    children = {}
    for pk, parent in query.values_list("pk", "parent_id"):
        children.setdefault(parent, []).append(pk)
    found, pending = set(), [folder.pk]
    while pending:
        pk = pending.pop()
        if pk in found:
            continue
        found.add(pk)
        pending.extend(children.get(pk, []))
    return found


def ancestors(folder):
    result, seen = [], set()
    while folder and folder.pk not in seen:
        seen.add(folder.pk)
        result.append(folder)
        folder = folder.parent
    return list(reversed(result))


def folder_path(folder):
    return " / ".join([folder.group.name] + [item.name for item in ancestors(folder)]) if folder else "待整理"


def create_folder(group, parent, name, user, description=""):
    name = clean_name(name)
    if parent and (parent.group_id != group.pk or parent.deleted_at):
        raise ValidationError("上级目录不属于当前项目或已删除。")
    # Serializes sibling creation on PostgreSQL; unique constraints guard collisions.
    with transaction.atomic():
        ArchiveGroup.objects.select_for_update().get(pk=group.pk)
        if Folder.objects.filter(group=group, parent=parent, normalized_name=name.casefold(), deleted_at__isnull=True).exists():
            raise ValidationError("同级目录已有相同名称，请更换名称。")
        return Folder.objects.create(group=group, parent=parent, name=name, description=description, created_by=user)


def upload_parts(raw_path):
    if not raw_path:
        return []
    if len(raw_path) > 2048 or "\\" in raw_path or raw_path.startswith("/"):
        raise ValidationError("文件夹路径无效。")
    parts = raw_path.split("/")
    if len(parts) > 21 or any(not part or part in {".", ".."} for part in parts):
        raise ValidationError("文件夹路径无效或层级超过 20 层。")
    for part in parts[:-1]:
        clean_name(part)
    if any(ord(char) < 32 for char in parts[-1]):
        raise ValidationError("文件名包含无效字符。")
    return parts


def resolve_upload_folder(base, parts, user):
    parent = base
    for name in parts[:-1]:
        existing = Folder.objects.filter(group=base.group, parent=parent, normalized_name=clean_name(name).casefold(), deleted_at__isnull=True).first()
        parent = existing or create_folder(base.group, parent, name, user)
    return parent
