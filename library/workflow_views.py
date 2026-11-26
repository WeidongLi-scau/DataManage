import secrets
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .forms import PersonalProfileForm
from .management_views import staff_view
from .models import ArchiveGroup, AuditLog, Material, MaterialCategory, MaterialCollection, MaterialVersion, UploadBatch, UserAccountState
from .permissions import accessible_collections, accessible_groups, accessible_materials, can_manage_material
from .services.exports import export_response


def audit(user, action, obj, **details):
    AuditLog.objects.create(actor=user, action=action, object_type=obj.__class__.__name__, object_id=str(obj.pk), details=details)


@login_required
@never_cache
def personal_settings(request):
    profile_form = PersonalProfileForm(request.POST if request.method == "POST" and request.POST.get("section") == "profile" else None, instance=request.user)
    password_form = PasswordChangeForm(request.user, request.POST if request.method == "POST" and request.POST.get("section") == "password" else None)
    if request.method == "POST":
        if request.POST.get("section") == "profile" and profile_form.is_valid():
            profile_form.save()
            audit(request.user, "account.profile.update", request.user)
            messages.success(request, "个人信息已保存。")
            return redirect("personal_settings")
        if request.POST.get("section") == "password" and password_form.is_valid():
            with transaction.atomic():
                user = password_form.save()
                UserAccountState.objects.update_or_create(user=user, defaults={"must_change_password": False})
            update_session_auth_hash(request, user)
            audit(user, "account.password.changed", user)
            messages.success(request, "密码已修改，当前登录保持有效。")
            return redirect("personal_settings")
    return render(request, "library/personal_settings.html", {"profile_form": profile_form, "password_form": password_form})


@staff_view
@never_cache
def reset_member_password(request, user_id):
    member = get_object_or_404(get_user_model(), pk=user_id)
    if member.pk == request.user.pk:
        return redirect("personal_settings")
    if member.is_superuser and not request.user.is_superuser:
        raise Http404
    credentials = None
    if request.method == "POST":
        with transaction.atomic():
            member = get_user_model().objects.select_for_update().get(pk=member.pk)
            password = secrets.token_urlsafe(18)
            member.set_password(password)
            member.save(update_fields=["password"])
            UserAccountState.objects.update_or_create(user=member, defaults={"must_change_password": True})
            audit(request.user, "account.password.reset", member)
        credentials = {"username": member.username, "password": password}
    return render(request, "library/reset_password.html", {"member": member, "credentials": credentials})


@login_required
def projects(request, group_id=None):
    groups = accessible_groups(request.user).prefetch_related("managers")
    if group_id is None:
        counts = dict(accessible_materials(request.user).values("groups").annotate(total=Count("pk", distinct=True)).values_list("groups", "total"))
        for group in groups:
            group.visible_material_count = counts.get(group.pk, 0)
        return render(request, "library/projects.html", {"groups": groups})
    group = get_object_or_404(groups, pk=group_id)
    materials = accessible_materials(request.user).filter(groups=group).select_related("category", "publication").order_by("-updated_at")
    collections = accessible_collections(request.user).filter(groups=group)
    return render(request, "library/project_detail.html", {"group": group, "collections": collections, "page_obj": Paginator(materials, 20).get_page(request.GET.get("page")), "material_count": materials.count()})


@login_required
def my_upload_batches(request):
    batches = UploadBatch.objects.filter(created_by=request.user).annotate(item_count=Count("items")).order_by("-created_at", "-pk")
    return render(request, "library/my_upload_batches.html", {"page_obj": Paginator(batches, 20).get_page(request.GET.get("page"))})


@login_required
def restore_version(request, material_id, version_id):
    material = get_object_or_404(accessible_materials(request.user), pk=material_id)
    if not can_manage_material(request.user, material):
        raise Http404
    source = get_object_or_404(material.versions, pk=version_id)
    if request.method == "POST":
        with transaction.atomic():
            material = Material.objects.select_for_update().get(pk=material.pk)
            number = material.versions.order_by("-version_number").values_list("version_number", flat=True).first() + 1
            version = MaterialVersion.objects.create(
                material=material, version_number=number, original_file=source.original_file.name,
                stored_file=source.stored_file, preview_file=source.preview_file.name,
                original_name=source.original_name, extension=source.extension,
                content_type=source.content_type, file_size=source.file_size, sha256=source.sha256,
                extracted_text=source.extracted_text, status=source.status, error_message=source.error_message,
                created_by=request.user,
            )
            material.current_version = version
            material.save(update_fields=["current_version", "updated_at"])
            audit(request.user, "material.version.restore", material, source_version=source.version_number, new_version=number)
            from .views import _enqueue_processing
            transaction.on_commit(lambda: _enqueue_processing(version.pk))
        messages.success(request, f"已将 v{source.version_number} 恢复为新版本 v{number}，全部历史仍保留。")
        return redirect("material_detail", material_id=material.pk)
    return render(request, "library/restore_version.html", {"material": material, "source": source})


def selected_materials(request):
    raw_ids = request.POST.getlist("materials")
    if not raw_ids or len(raw_ids) > 200 or any(not value.isdigit() for value in raw_ids):
        return None
    ids = {int(value) for value in raw_ids}
    materials = list(accessible_materials(request.user).filter(pk__in=ids).select_related("category", "publication").prefetch_related("collections"))
    if len(materials) != len(ids):
        raise Http404
    return materials


@login_required
@require_POST
def bulk_materials(request):
    materials = selected_materials(request)
    if materials is None:
        messages.error(request, "请先选择 1–200 份资料。")
        return redirect("home")
    action = request.POST.get("action", "")
    editable = all(can_manage_material(request.user, item) for item in materials)
    context = {"materials": materials, "editable": editable, "categories": MaterialCategory.objects.filter(is_active=True), "collections": accessible_collections(request.user), "groups": accessible_groups(request.user), "action": action}
    if action == "download":
        size = sum(item.current_version.file_size for item in materials if item.current_version_id)
        if size > 1024 ** 3:
            context["error"] = "打包总量超过 1 GB，请分批下载。"
        elif any(not item.current_version_id for item in materials):
            context["error"] = "部分资料没有可下载版本，请重新选择。"
        else:
            output = tempfile.TemporaryFile()
            try:
                with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                    for item in materials:
                        version = item.current_version
                        name = Path(version.original_name.replace("\\", "/")).name
                        with version.original_file.open("rb") as source, archive.open(f"{item.pk}/{name}", "w", force_zip64=True) as target:
                            while chunk := source.read(1024 * 1024):
                                target.write(chunk)
                output.seek(0)
            except OSError:
                output.close()
                context["error"] = "部分原文件暂时无法读取，请检查后重试。"
            else:
                audit(request.user, "material.bulk.download", materials[0], material_ids=[item.pk for item in materials])
                response = FileResponse(output, as_attachment=True, filename="team-materials.zip", content_type="application/zip")
                response["Cache-Control"] = "no-store"
                return response
    elif action:
        if not editable:
            raise Http404
        actions = {"category": "修改分类", "add_collection": "加入集合", "remove_collection": "移出集合", "add_group": "增加项目授权", "remove_group": "移除项目授权", "delete": "移入回收站"}
        target = None
        if action not in actions:
            context["error"] = "请选择有效操作。"
        else:
            target_id = request.POST.get("target", "")
            if action == "category":
                if target_id and not target_id.isdigit():
                    context["error"] = "请选择有效分类。"
                else:
                    target = get_object_or_404(context["categories"], pk=target_id) if target_id else None
            elif action.endswith("collection"):
                target = get_object_or_404(context["collections"], pk=target_id) if target_id.isdigit() else None
                if target is None:
                    context["error"] = "请选择资料集合。"
                elif action == "add_collection" and any(not item.groups.filter(pk__in=target.groups.values("pk")).exists() for item in materials):
                    context["error"] = "所选集合与部分资料的项目组不匹配，请分开处理。"
            elif action.endswith("group"):
                target = get_object_or_404(context["groups"], pk=target_id) if target_id.isdigit() else None
                if target is None:
                    context["error"] = "请选择项目组。"
                elif action == "remove_group":
                    for item in materials:
                        remaining = item.groups.exclude(pk=target.pk)
                        if not remaining.exists() or item.collections.exclude(groups__in=remaining).exists():
                            context["error"] = "移除后会有资料失去全部项目归属，或集合与项目不再匹配，请先逐项调整。"
                            break
            if "error" not in context:
                context.update(confirm=True, action_label=actions[action], target=target)
                if request.POST.get("confirmed") == "yes":
                    with transaction.atomic():
                        locked = list(Material.objects.select_for_update().filter(pk__in=[item.pk for item in materials]).order_by("pk"))
                        visible_ids = set(accessible_materials(request.user).filter(pk__in=[item.pk for item in locked]).values_list("pk", flat=True))
                        if len(visible_ids) != len(materials) or any(not can_manage_material(request.user, item) for item in locked):
                            raise Http404
                        for item in locked:
                            if action == "add_collection" and not item.groups.filter(pk__in=target.groups.values("pk")).exists():
                                raise Http404
                            if action == "remove_group":
                                remaining = item.groups.exclude(pk=target.pk)
                                if not remaining.exists() or item.collections.exclude(groups__in=remaining).exists():
                                    raise Http404
                            if action == "category":
                                item.category = target
                            elif action == "add_collection":
                                item.collections.add(target)
                            elif action == "remove_collection":
                                item.collections.remove(target)
                            elif action == "add_group":
                                item.groups.add(target)
                            elif action == "remove_group":
                                item.groups.remove(target)
                            elif action == "delete":
                                item.deleted_at = timezone.now()
                                item.deleted_by = request.user
                            changed_fields = ["updated_at"]
                            if action == "category":
                                changed_fields.append("category")
                            elif action == "delete":
                                changed_fields.extend(["deleted_at", "deleted_by"])
                            item.save(update_fields=changed_fields)
                            audit(request.user, f"material.bulk.{action}", item, target_id=target.pk if target else None)
                            if item.current_version_id:
                                from .search import index_version
                                transaction.on_commit(lambda version=item.current_version: index_version(version))
                    messages.success(request, f"已完成：{actions[action]}，共 {len(materials)} 份资料。")
                    return redirect("home")
    return render(request, "library/bulk_materials.html", context)


@login_required
def publications(request):
    items = accessible_materials(request.user).filter(record_type=Material.RecordType.PUBLICATION).select_related("publication", "publication__journal", "category").prefetch_related("collections").order_by("-publication__publication_year", "-created_at")
    params = {key: request.GET.get(key, "").strip() for key in ("year", "author", "journal", "group")}
    if params["year"].isdigit():
        items = items.filter(publication__publication_year=int(params["year"]))
    if params["author"]:
        items = items.filter(publication__authors__icontains=params["author"])
    if params["journal"]:
        from django.db.models import Q
        items = items.filter(Q(publication__journal_name__icontains=params["journal"]) | Q(publication__journal__name__icontains=params["journal"]))
    if params["group"].isdigit():
        group = get_object_or_404(accessible_groups(request.user), pk=params["group"])
        items = items.filter(groups=group)
    items = items.distinct()
    export_format = request.GET.get("format")
    if export_format in {"csv", "xlsx", "bib"}:
        audit(request.user, "publication.export", request.user, format=export_format, count=items.count())
        return export_response(items, export_format)
    return render(request, "library/publications.html", {"page_obj": Paginator(items, 20).get_page(request.GET.get("page")), "filters": params, "filter_query": urlencode(params), "groups": accessible_groups(request.user)})
