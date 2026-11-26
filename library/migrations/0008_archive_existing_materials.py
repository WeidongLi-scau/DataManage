from django.db import migrations


def archive_existing(apps, schema_editor):
    Group = apps.get_model("library", "ArchiveGroup")
    Folder = apps.get_model("library", "Folder")
    Material = apps.get_model("library", "Material")
    Collection = apps.get_model("library", "MaterialCollection")
    db = schema_editor.connection.alias
    roots = {}
    for group in Group.objects.using(db).all():
        roots[group.pk] = Folder.objects.using(db).create(group=group, name="待整理资料", normalized_name="待整理资料")
    collection_folders = {}
    for collection in Collection.objects.using(db).filter(is_active=True).order_by("pk"):
        for group in collection.groups.using(db).all():
            # Collection names were not unique; preserve every collection explicitly.
            name = "".join(char for char in collection.name.replace("/", "／").replace("\\", "＼") if ord(char) >= 32).strip() or f"集合{collection.pk}"
            if name in {".", ".."}:
                name = f"集合{collection.pk}"
            if Folder.objects.using(db).filter(group=group, parent=None, normalized_name=name.casefold()).exists():
                name = f"{name[:140]}（集合{collection.pk}）"
            collection_folders[(collection.pk, group.pk)] = Folder.objects.using(db).create(group=group, name=name, normalized_name=name.casefold(), description=collection.description, created_by_id=collection.created_by_id)
    for material in Material.objects.using(db).filter(folder__isnull=True).iterator():
        # A stable primary location; all existing authorization and collection links remain.
        group = material.groups.using(db).order_by("is_public", "pk").first()
        if group is None:
            group, _ = Group.objects.using(db).get_or_create(name="历史未归档资料", defaults={"is_active": False})
            if group.pk not in roots:
                roots[group.pk] = Folder.objects.using(db).create(group=group, name="待整理资料", normalized_name="待整理资料")
        folder = roots[group.pk]
        if material.groups.using(db).count() == 1:
            for collection_id in material.collections.using(db).order_by("pk").values_list("pk", flat=True):
                if (collection_id, group.pk) in collection_folders:
                    folder = collection_folders[(collection_id, group.pk)]
                    break
        Material.objects.using(db).filter(pk=material.pk).update(folder=folder)


class Migration(migrations.Migration):
    dependencies = [("library", "0007_project_folders")]
    operations = [migrations.RunPython(archive_existing, migrations.RunPython.noop)]
