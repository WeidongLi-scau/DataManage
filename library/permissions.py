from django.db.models import Q

from .models import ArchiveGroup, Material, MaterialCollection


def accessible_groups(user):
    groups = ArchiveGroup.objects.filter(is_active=True)
    if user.is_superuser or user.is_staff:
        return groups
    return groups.filter(Q(members=user) | Q(is_public=True)).distinct()


def accessible_materials(user, include_deleted=False):
    queryset = Material.objects.select_related("current_version", "uploaded_by").prefetch_related("groups")
    if not include_deleted:
        queryset = queryset.filter(deleted_at__isnull=True)
    if user.is_superuser or user.is_staff:
        return queryset
    return queryset.filter(groups__in=accessible_groups(user)).distinct()


def can_manage_material(user, material):
    return user.is_superuser or user.is_staff or material.uploaded_by_id == user.id


def manageable_groups(user):
    groups = ArchiveGroup.objects.filter(is_active=True)
    if user.is_superuser or user.is_staff:
        return groups
    return groups.filter(managers=user).distinct()


def accessible_collections(user):
    return MaterialCollection.objects.filter(is_active=True, groups__in=accessible_groups(user)).distinct()


def can_manage_collection(user, collection):
    return user.is_superuser or user.is_staff or collection.groups.filter(managers=user).exists()

