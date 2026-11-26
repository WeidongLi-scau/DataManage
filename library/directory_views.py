import tempfile
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import ArchiveGroup, Folder, Material
from .permissions import accessible_groups, accessible_materials, can_manage_material
from .search import index_version
from .services.folders import ancestors, can_create, can_manage_folder, clean_name, create_folder, descendants, folder_path, visible_folders
from .workflow_views import audit


def choices(user, group=None):
    folders = list(visible_folders(user).filter(group=group) if group else visible_folders(user))
    by_id = {folder.pk: folder for folder in folders}
    def path(folder):
        names, seen = [], set()
        while folder and folder.pk not in seen:
            seen.add(folder.pk)
            names.append(folder.name)
            folder = by_id.get(folder.parent_id)
        return " / ".join(reversed(names))
    for folder in folders:
        folder.display_path = path(folder)
    return sorted(folders, key=lambda folder: (folder.group_id, folder.display_path.casefold()))


@login_required
def browse(request, group_id, folder_id=None):
    group = get_object_or_404(accessible_groups(request.user), pk=group_id)
    folder = get_object_or_404(visible_folders(request.user), pk=folder_id, group=group) if folder_id else None
    all_folders = choices(request.user, group)
    nodes = {item.pk: {"folder": item, "children": []} for item in all_folders}
    tree_nodes = []
    for item in all_folders:
        if item.parent_id in nodes:
            nodes[item.parent_id]["children"].append(nodes[item.pk])
        else:
            tree_nodes.append(nodes[item.pk])
    query = request.GET.get("q", "").strip()[:200]
    scope = request.GET.get("scope", "subtree")
    if scope not in {"current", "subtree", "all"}:
        scope = "subtree"
    folder_ids = descendants(folder) if folder else {item.pk for item in all_folders}
    materials = accessible_materials(request.user).select_related("folder__group", "category")
    if query:
        from .search import search_materials
        results = search_materials(request.user, query)
        ids = [item.pk for item in results] if isinstance(results, list) else results.values("pk")
        materials = materials.filter(pk__in=ids)
        found_folders = visible_folders(request.user).filter(Q(name__icontains=query) | Q(description__icontains=query))
        if scope == "current":
            materials = materials.filter(folder=folder) if folder else materials.filter(folder__group=group, folder__parent__isnull=True)
            found_folders = found_folders.filter(group=group, parent=folder)
        elif scope == "subtree":
            materials = materials.filter(folder_id__in=folder_ids)
            found_folders = found_folders.filter(pk__in=folder_ids)
    else:
        materials = materials.filter(folder=folder) if folder else materials.none()
        found_folders = visible_folders(request.user).filter(group=group, parent=folder)
    page = Paginator(materials.order_by("-updated_at", "pk"), 30).get_page(request.GET.get("page"))
    allowed_group_ids = set(accessible_groups(request.user).values_list("pk", flat=True))
    for item in page:
        item.can_open_folder = bool(item.folder and item.folder.group_id in allowed_group_ids and item.folder.deleted_at is None)
        item.visible_path = folder_path(item.folder) if item.can_open_folder else "原归档项目不可访问，可按现有资料授权查看"
        item.can_manage = can_manage_material(request.user, item)
    cross = accessible_materials(request.user).filter(groups=group).exclude(folder__group=group) if not folder and not query else Material.objects.none()
    return render(request, "library/directory.html", {
        "group": group, "folder": folder, "breadcrumbs": ancestors(folder), "tree_nodes": tree_nodes,
        "folders": found_folders.order_by("name"), "page_obj": page, "query": query, "scope": scope,
        "filter_query": urlencode({"q": query, "scope": scope}), "can_create": can_create(request.user, group),
        "can_manage_folder": folder and can_manage_folder(request.user, folder), "cross_materials": cross,
    })


def subtree_records(folder):
    ids = descendants(folder)
    return ids, Material.objects.filter(folder_id__in=ids, deleted_at__isnull=True)


@login_required
def folder_action(request, group_id, folder_id=None, action="create"):
    group = get_object_or_404(accessible_groups(request.user), pk=group_id)
    folder = get_object_or_404(visible_folders(request.user), pk=folder_id, group=group) if folder_id else None
    if action == "create":
        if not can_create(request.user, group):
            raise Http404
    elif not folder or not can_manage_folder(request.user, folder):
        raise Http404
    affected_ids, records = subtree_records(folder) if folder else (set(), Material.objects.none())
    if action in {"move", "delete"} and any(not can_manage_material(request.user, item) for item in records):
        messages.error(request, "此目录包含你无权管理的资料，请联系项目负责人或管理员处理。")
        return redirect("folder_browse", group_id=group.pk, folder_id=folder.pk)
    targets = choices(request.user) if action == "move" and (request.user.is_staff or request.user.is_superuser) else choices(request.user, group)
    targets = [item for item in targets if item.pk not in affected_ids]
    error = None
    if request.method == "POST":
        try:
            with transaction.atomic():
                ArchiveGroup.objects.select_for_update().get(pk=group.pk)
                if folder:
                    folder = get_object_or_404(Folder.objects.select_for_update(), pk=folder.pk, deleted_at__isnull=True)
                if action == "create":
                    new_folder = create_folder(group, folder, request.POST.get("name", ""), request.user, request.POST.get("description", "")[:500])
                    audit(request.user, "folder.create", new_folder)
                    return redirect("folder_browse", group_id=group.pk, folder_id=new_folder.pk)
                if action in {"rename", "move"}:
                    name = clean_name(request.POST.get("name", folder.name))
                    parent = folder.parent
                    if action == "move":
                        target = request.POST.get("parent", "")
                        parent = get_object_or_404(visible_folders(request.user), pk=target) if target.isdigit() else None
                        if parent and parent.pk in descendants(folder):
                            raise ValidationError("不能移动到自身或自己的子文件夹。")
                    target_group = parent.group if parent else group
                    cross = target_group.pk != group.pk
                    if cross and (not (request.user.is_staff or request.user.is_superuser) or request.POST.get("authorization_confirmed") != "yes"):
                        raise ValidationError("跨项目移动需要管理员明确确认授权变化。")
                    if Folder.objects.filter(group=target_group, parent=parent, normalized_name=name.casefold(), deleted_at__isnull=True).exclude(pk=folder.pk).exists():
                        raise ValidationError("目标位置已有同名文件夹，请更换名称。")
                    if cross:
                        if Folder.objects.filter(pk__in=descendants(folder, include_deleted=True), deleted_at__isnull=False).exists():
                            raise ValidationError("请先恢复目录内已删除的子文件夹，再执行跨项目移动。")
                        Folder.objects.filter(pk__in=affected_ids).update(group=target_group)
                        for item in Material.objects.filter(folder_id__in=affected_ids):
                            item.groups.set([target_group])
                            item.collections.clear()
                            item.save(update_fields=["updated_at"])
                            if item.current_version_id:
                                transaction.on_commit(lambda version=item.current_version: index_version(version))
                        folder.group = target_group
                    folder.name, folder.parent = name, parent
                    if action == "rename":
                        folder.description = request.POST.get("description", "")[:500]
                    folder.save()
                    audit(request.user, f"folder.{action}", folder)
                elif action == "delete":
                    token, now = uuid.uuid4(), timezone.now()
                    Folder.objects.filter(pk__in=affected_ids).update(deleted_at=now, deletion_batch=token)
                    for item in records:
                        item.deleted_at, item.deleted_by, item.folder_deletion_batch = now, request.user, token
                        item.save(update_fields=["deleted_at", "deleted_by", "folder_deletion_batch", "updated_at"])
                        if item.current_version_id:
                            transaction.on_commit(lambda version=item.current_version: index_version(version))
                    audit(request.user, "folder.delete", folder, folders=len(affected_ids), materials=records.count())
                    messages.success(request, "目录及其资料已移入回收站。")
                    return redirect("folder_root", group_id=group.pk)
            return redirect("folder_browse", group_id=folder.group_id, folder_id=folder.pk)
        except (ValidationError, IntegrityError) as exc:
            error = "；".join(exc.messages) if isinstance(exc, ValidationError) else "同级已有同名文件夹，请更换名称。"
    return render(request, "library/folder_action.html", {"group": group, "folder": folder, "action": action, "targets": targets, "error": error, "affected_count": records.count(), "folder_count": len(affected_ids)})


@login_required
def move_materials(request):
    from .workflow_views import selected_materials
    items = selected_materials(request) if request.method == "POST" else None
    if not items or any(not can_manage_material(request.user, item) for item in items):
        raise Http404
    targets = choices(request.user)
    error = None
    if request.POST.get("confirmed") == "yes":
        target_id = request.POST.get("folder", "")
        if not target_id.isdigit():
            raise Http404
        target = get_object_or_404(visible_folders(request.user), pk=target_id)
        cross = any(item.folder and item.folder.group_id != target.group_id for item in items)
        if cross and not (request.user.is_staff or request.user.is_superuser):
            error = "跨项目移动需要管理员操作。"
        elif cross and request.POST.get("authorization_confirmed") != "yes":
            error = "跨项目移动将替换原项目授权，请明确确认后再操作。"
        else:
            with transaction.atomic():
                for item in items:
                    locked = get_object_or_404(Material.objects.select_for_update(), pk=item.pk, deleted_at__isnull=True)
                    if not can_manage_material(request.user, locked):
                        raise Http404
                    if locked.folder and locked.folder.group_id != target.group_id:
                        locked.groups.set([target.group])
                        locked.collections.clear()
                    elif not locked.folder:
                        locked.groups.add(target.group)
                    locked.folder = target
                    locked.save(update_fields=["folder", "updated_at"])
                    audit(request.user, "material.folder.move", locked, folder_id=target.pk)
                    if locked.current_version_id:
                        transaction.on_commit(lambda version=locked.current_version: index_version(version))
            messages.success(request, f"已移动 {len(items)} 份资料，历史版本保留。")
            return redirect("folder_browse", group_id=target.group_id, folder_id=target.pk)
    return render(request, "library/move_materials.html", {"materials": items, "targets": targets, "error": error})


@login_required
def folder_download(request, folder_id, selected_roots=None):
    folder = get_object_or_404(visible_folders(request.user), pk=folder_id)
    folder_ids = set().union(*(descendants(root) for root in (selected_roots or [folder])))
    items = list(accessible_materials(request.user).filter(folder_id__in=folder_ids).select_related("folder", "current_version"))
    if len(items) > 2000 or sum(item.current_version.file_size for item in items if item.current_version_id) > 1024 ** 3:
        messages.error(request, "目录打包超过 2000 份或 1 GB，请选择子目录分批下载。")
        return redirect("folder_browse", group_id=folder.group_id, folder_id=folder.pk)
    output = tempfile.TemporaryFile()
    try:
        folders = {item.pk: item for item in Folder.objects.filter(pk__in=folder_ids)}
        def relative(item):
            names = []
            root_id = item.pk
            while item:
                root_id = item.pk
                names.append(item.name)
                item = folders.get(item.parent_id)
            path = "/".join(reversed(names))
            if selected_roots and len(selected_roots) > 1:
                path = f"目录{root_id}/" + path
            return path
        with zipfile.ZipFile(output, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
            for node in folders.values():
                archive.writestr(relative(node) + "/", b"")
            used = set()
            for item in items:
                version = item.current_version
                if not version:
                    continue
                name = Path(version.original_name.replace("\\", "/")).name
                archive_name = relative(folders[item.folder_id]) + "/" + name
                if archive_name.casefold() in used:
                    archive_name = relative(folders[item.folder_id]) + f"/{item.pk}-" + name
                while archive_name.casefold() in used:
                    archive_name = relative(folders[item.folder_id]) + f"/{uuid.uuid4().hex[:8]}-" + name
                used.add(archive_name.casefold())
                with version.original_file.open("rb") as source, archive.open(archive_name, "w", force_zip64=True) as destination:
                    while chunk := source.read(1024 * 1024):
                        destination.write(chunk)
        output.seek(0)
    except OSError:
        output.close()
        messages.error(request, "部分原文件暂时无法读取，请检查后重试。")
        return redirect("folder_browse", group_id=folder.group_id, folder_id=folder.pk)
    audit(request.user, "folder.download", folder, count=len(items))
    response = FileResponse(output, as_attachment=True, filename=f"{folder.name}.zip", content_type="application/zip")
    response["Cache-Control"] = "no-store"
    return response


@login_required
def folder_restore(request, folder_id):
    if not (request.user.is_staff or request.user.is_superuser):
        raise Http404
    folder = get_object_or_404(Folder, pk=folder_id, deleted_at__isnull=False)
    if folder.parent and folder.parent.deletion_batch == folder.deletion_batch:
        messages.error(request, "请先恢复本批次的最上层文件夹。")
        return redirect("recycle_bin")
    error = None
    if request.method == "POST":
        try:
            with transaction.atomic():
                ArchiveGroup.objects.select_for_update().get(pk=folder.group_id)
                folder = get_object_or_404(Folder.objects.select_for_update(), pk=folder.pk, deleted_at__isnull=False)
                name = clean_name(request.POST.get("name", folder.name))
                parent_id = request.POST.get("parent", "")
                parent = get_object_or_404(visible_folders(request.user), pk=parent_id, group_id=folder.group_id) if parent_id.isdigit() else None
                token = folder.deletion_batch
                nodes = Folder.objects.filter(deletion_batch=token, deleted_at__isnull=False)
                if not token:
                    raise ValidationError("目录缺少恢复批次，请联系管理员检查。")
                if Folder.objects.filter(group_id=folder.group_id, parent=parent, normalized_name=name.casefold(), deleted_at__isnull=True).exists():
                    raise ValidationError("恢复位置已有同名目录，请修改名称或选择其他位置。")
                folder.name, folder.parent = name, parent
                folder.save()
                nodes.update(deleted_at=None, deletion_batch=None)
                for item in Material.objects.filter(folder_deletion_batch=token):
                    item.deleted_at = item.deleted_by = item.folder_deletion_batch = None
                    item.save(update_fields=["deleted_at", "deleted_by", "folder_deletion_batch", "updated_at"])
                    if item.current_version_id:
                        transaction.on_commit(lambda version=item.current_version: index_version(version))
                audit(request.user, "folder.restore", folder)
            messages.success(request, "目录层级及本次删除的资料已恢复。")
            return redirect("folder_browse", group_id=folder.group_id, folder_id=folder.pk)
        except (ValidationError, IntegrityError) as exc:
            error = "；".join(exc.messages) if isinstance(exc, ValidationError) else "恢复位置发生名称冲突。"
    return render(request, "library/folder_restore.html", {"folder": folder, "targets": choices(request.user, folder.group), "error": error})


@login_required
def batch_folders(request):
    if request.method != "POST":
        raise Http404
    raw = request.POST.getlist("folders")
    if not raw or len(raw) > 100 or any(not value.isdigit() for value in raw):
        messages.error(request, "请选择 1–100 个文件夹。")
        return redirect("projects")
    selected = list(visible_folders(request.user).filter(pk__in={int(value) for value in raw}))
    if len(selected) != len(set(raw)):
        raise Http404
    selected_ids = {item.pk for item in selected}
    roots = [item for item in selected if not any(parent.pk in selected_ids for parent in ancestors(item)[:-1])]
    operation = request.POST.get("operation", "")
    if operation == "download":
        return folder_download(request, roots[0].pk, roots)
    if any(not can_manage_folder(request.user, item) for item in roots):
        raise Http404
    for item in roots:
        _, records = subtree_records(item)
        if any(not can_manage_material(request.user, record) for record in records):
            raise Http404
    excluded = set().union(*(descendants(item) for item in roots))
    targets = [item for item in choices(request.user) if item.pk not in excluded]
    error = None
    if request.POST.get("confirmed") == "yes":
        try:
            with transaction.atomic():
                list(Folder.objects.select_for_update().filter(pk__in=excluded).order_by("pk"))
                if operation == "move":
                    target_id = request.POST.get("parent", "")
                    if not target_id.isdigit():
                        raise ValidationError("请选择目标文件夹。")
                    target = get_object_or_404(visible_folders(request.user), pk=target_id)
                    if target.pk in excluded:
                        raise ValidationError("不能移动到所选文件夹或其子目录。")
                    if any(item.group_id != target.group_id for item in roots):
                        raise ValidationError("批量目录移动仅限同一项目，跨项目请逐目录确认授权。")
                    names = set()
                    for item in roots:
                        if item.normalized_name in names or Folder.objects.filter(group=target.group, parent=target, normalized_name=item.normalized_name, deleted_at__isnull=True).exclude(pk__in=selected_ids).exists():
                            raise ValidationError("目标位置存在同名目录，请重命名后再移动。")
                        names.add(item.normalized_name)
                    for item in roots:
                        item.parent = target
                        item.save()
                        audit(request.user, "folder.batch.move", item, parent_id=target.pk)
                elif operation == "delete":
                    now = timezone.now()
                    for item in roots:
                        ids, records = subtree_records(item)
                        token = uuid.uuid4()
                        Folder.objects.filter(pk__in=ids).update(deleted_at=now, deletion_batch=token)
                        for record in records:
                            record.deleted_at, record.deleted_by, record.folder_deletion_batch = now, request.user, token
                            record.save(update_fields=["deleted_at", "deleted_by", "folder_deletion_batch", "updated_at"])
                            if record.current_version_id:
                                transaction.on_commit(lambda version=record.current_version: index_version(version))
                        audit(request.user, "folder.batch.delete", item)
                else:
                    raise ValidationError("请选择移动、删除或下载操作。")
            messages.success(request, f"已处理 {len(roots)} 个文件夹，子目录和资料一起处理。")
            return redirect("folder_root", group_id=roots[0].group_id)
        except (ValidationError, IntegrityError) as exc:
            error = "；".join(exc.messages) if isinstance(exc, ValidationError) else "文件夹名称冲突，请检查目标位置。"
    return render(request, "library/batch_folders.html", {"folders": roots, "targets": targets, "operation": operation, "error": error})
