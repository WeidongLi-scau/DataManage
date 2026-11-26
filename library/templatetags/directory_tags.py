from django import template
from library.permissions import accessible_groups
from library.services.folders import folder_path

register = template.Library()


@register.simple_tag(takes_context=True)
def archive_location(context, material):
    folder = material.folder
    if folder and folder.deleted_at is None and accessible_groups(context["request"].user).filter(pk=folder.group_id).exists():
        return {"folder": folder, "path": folder_path(folder)}
    return None
