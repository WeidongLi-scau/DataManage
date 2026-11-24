import logging
import mimetypes
import shutil
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.core.paginator import Paginator
from django.db import transaction
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import content_disposition_header
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .forms import LoginForm, MaterialMetadataForm, UploadMetadataForm
from .models import ArchiveGroup, AuditLog, Material, MaterialVersion
from .permissions import accessible_groups, accessible_materials, can_manage_material
from .search import search_materials

logger = logging.getLogger(__name__)


def _audit(request, action, obj=None, **details):
    AuditLog.objects.create(
        actor=request.user if request.user.is_authenticated else None,
        action=action,
        object_type=obj.__class__.__name__ if obj else "",
        object_id=str(obj.pk) if obj else "",
        details=details,
    )


def _group_queryset(user):
    return accessible_groups(user).order_by("name")


def login_view(request):
    if request.user.is_authenticated:
        return redirect("home")
    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = authenticate(
            request,
            username=form.cleaned_data["username"],
            password=form.cleaned_data["password"],
        )
        if user is not None and user.is_active:
            first_login = user.last_login is None
            login(request, user)
            AuditLog.objects.create(actor=user, action="login.success")
            if first_login:
                request.session["must_change_password"] = True
                return redirect("first_password_change")
            target = request.POST.get("next") or request.GET.get("next", "")
            if url_has_allowed_host_and_scheme(target, {request.get_host()}, require_https=request.is_secure()):
                return redirect(target)
            return redirect("home")
        AuditLog.objects.create(
            action="login.failure",
            details={
                "username": form.cleaned_data["username"],
                "remote_ip": request.META.get("REMOTE_ADDR", ""),
            },
        )
        form.add_error(None, "用户名或密码不正确，或账户已停用。")
    return render(request, "library/login.html", {"form": form})


@login_required
def first_password_change(request):
    if not request.session.get("must_change_password"):
        return redirect("home")
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        request.session.pop("must_change_password", None)
        _audit(request, "account.initial_password.changed")
        messages.success(request, "密码已更新。")
        return redirect("home")
    return render(request, "library/password_change.html", {"form": form})


@login_required
@require_POST
def logout_view(request):
    _audit(request, "logout")
    logout(request)
    return redirect("login")


@login_required
def home(request):
    query = request.GET.get("q", "").strip()[:200]
    results = search_materials(request.user, query)
    extension = request.GET.get("type", "").strip().lower().lstrip(".")
    category = request.GET.get("category", "").strip()[:120]
    group_id = request.GET.get("group", "").strip()
    uploader_id = request.GET.get("uploader", "").strip()
    uploaded_after = parse_date(request.GET.get("after", ""))
    uploaded_before = parse_date(request.GET.get("before", ""))
    if isinstance(results, list):
        if extension:
            results = [item for item in results if item.extension.lstrip(".").lower() == extension]
        if category:
            results = [item for item in results if category.casefold() in item.category.casefold()]
        if group_id.isdigit():
            results = [item for item in results if item.groups.filter(pk=int(group_id)).exists()]
        if uploader_id.isdigit():
            results = [item for item in results if item.uploaded_by_id == int(uploader_id)]
        if uploaded_after:
            results = [item for item in results if item.created_at.date() >= uploaded_after]
        if uploaded_before:
            results = [item for item in results if item.created_at.date() <= uploaded_before]
    else:
        if extension:
            results = results.filter(current_version__extension__iexact=f".{extension}")
        if category:
            results = results.filter(category__icontains=category)
        if group_id.isdigit():
            results = results.filter(groups__id=int(group_id))
        if uploader_id.isdigit():
            results = results.filter(uploaded_by_id=int(uploader_id))
        if uploaded_after:
            results = results.filter(created_at__date__gte=uploaded_after)
        if uploaded_before:
            results = results.filter(created_at__date__lte=uploaded_before)
        results = results.distinct()
    paginator = Paginator(results, 20)
    page_obj = paginator.get_page(request.GET.get("page"))
    context = {
        "page_obj": page_obj,
        "query": query,
        "type_filter": extension,
        "category_filter": category,
        "group_filter": group_id,
        "uploader_filter": uploader_id,
        "uploaded_after": request.GET.get("after", ""),
        "uploaded_before": request.GET.get("before", ""),
        "groups": _group_queryset(request.user),
        "extensions": accessible_materials(request.user)
        .filter(current_version__isnull=False)
        .exclude(current_version__extension="")
        .order_by()
        .values_list("current_version__extension", flat=True)
        .distinct(),
        "uploaders": accessible_materials(request.user)
        .order_by("uploaded_by__username")
        .values("uploaded_by_id", "uploaded_by__username", "uploaded_by__first_name", "uploaded_by__last_name")
        .distinct(),
    }
    return render(request, "library/home.html", context)


@login_required
def upload(request):
    allowed_groups = _group_queryset(request.user)
    initial_groups = list(allowed_groups.filter(is_public=True).values_list("pk", flat=True))
    form = UploadMetadataForm(
        request.POST or None,
        group_queryset=allowed_groups,
        initial={"groups": initial_groups},
    )
    if request.method == "POST":
        files = request.FILES.getlist("files")
        if not files:
            form.add_error(None, "请选择至少一个文件。")
        if form.is_valid() and files:
            if not form.cleaned_data["groups"]:
                form.add_error("groups", "请至少选择一个可见分组。")
            oversized = [item.name for item in files if item.size > settings.MAX_UPLOAD_SIZE]
            if oversized:
                form.add_error(None, f"以下文件超过 100 MB 上限：{', '.join(oversized[:5])}")
            max_batch_size = getattr(settings, "MAX_BATCH_UPLOAD_SIZE", 1024 * 1024 * 1024)
            if sum(item.size for item in files) > max_batch_size:
                form.add_error(None, "本次批量上传总量超过 1 GB 上限，请分批上传。")
        if form.is_valid() and files and not form.errors:
            created_count = 0
            for uploaded in files:
                clean_name = Path(uploaded.name).name[:255]
                suffix = Path(clean_name).suffix.lower()[:20]
                title = form.cleaned_data["title"].strip() or Path(clean_name).stem[:255]
                with transaction.atomic():
                    material = Material.objects.create(
                        title=title,
                        description=form.cleaned_data["description"],
                        category=form.cleaned_data["category"],
                        tags=form.cleaned_data["tags"],
                        uploaded_by=request.user,
                    )
                    material.groups.set(form.cleaned_data["groups"])
                    version = MaterialVersion.objects.create(
                        material=material,
                        version_number=1,
                        original_file=uploaded,
                        original_name=clean_name,
                        extension=suffix,
                        content_type=uploaded.content_type or mimetypes.guess_type(clean_name)[0] or "application/octet-stream",
                        file_size=uploaded.size,
                        created_by=request.user,
                    )
                    material.current_version = version
                    material.save(update_fields=["current_version", "updated_at"])
                    _audit(request, "material.upload", material, file_name=clean_name, size=uploaded.size)
                    transaction.on_commit(lambda version_id=version.pk: _enqueue_processing(version_id))
                created_count += 1
            messages.success(request, f"已接收 {created_count} 个文件，正在后台处理。")
            return redirect("home")
    return render(request, "library/upload.html", {"form": form, "max_size_mb": settings.MAX_UPLOAD_SIZE // (1024 * 1024)})


def _enqueue_processing(version_id):
    from django_rq import enqueue

    try:
        enqueue("library.tasks.process_version", version_id, job_timeout=600)
    except Exception:
        logger.exception("Could not enqueue material processing job %s", version_id)
        version = MaterialVersion.objects.get(pk=version_id)
        version.status = MaterialVersion.Status.FAILED
        version.error_message = "后台处理服务暂不可用；原文件已保存，可稍后重试。"
        version.save(update_fields=["status", "error_message"])
        from .search import index_version

        index_version(version)


@login_required
@require_POST
def retry_processing(request, material_id, version_id):
    material = get_object_or_404(accessible_materials(request.user), pk=material_id)
    if not can_manage_material(request.user, material):
        raise Http404
    version = get_object_or_404(material.versions, pk=version_id)
    if version.status != MaterialVersion.Status.FAILED:
        messages.info(request, "该版本不需要重新处理。")
    else:
        version.status = MaterialVersion.Status.PENDING
        version.error_message = ""
        version.save(update_fields=["status", "error_message"])
        _enqueue_processing(version.pk)
        _audit(request, "material.processing.retry", material, version=version.version_number)
        messages.success(request, "已重新提交文件处理任务。")
    return redirect("material_detail", material_id=material.pk)


@login_required
def material_detail(request, material_id):
    material = get_object_or_404(accessible_materials(request.user), pk=material_id)
    return render(
        request,
        "library/detail.html",
        {"material": material, "versions": material.versions.select_related("created_by").all()},
    )


@login_required
def edit_material(request, material_id):
    material = get_object_or_404(accessible_materials(request.user), pk=material_id)
    if not can_manage_material(request.user, material):
        messages.error(request, "只有上传者或管理员可以编辑该资料。")
        return redirect("material_detail", material_id=material.pk)
    include_groups = request.user.is_staff or request.user.is_superuser
    form = MaterialMetadataForm(
        request.POST or None,
        group_queryset=ArchiveGroup.objects.filter(is_active=True) if include_groups else _group_queryset(request.user),
        include_groups=include_groups,
        initial={
            "title": material.title,
            "description": material.description,
            "category": material.category,
            "tags": material.tags,
            "groups": material.groups.all(),
        },
    )
    if request.method == "POST" and form.is_valid():
        if include_groups and not form.cleaned_data["groups"]:
            form.add_error("groups", "资料至少需要归属一个分组。")
        else:
            material.title = form.cleaned_data["title"]
            material.description = form.cleaned_data["description"]
            material.category = form.cleaned_data["category"]
            material.tags = form.cleaned_data["tags"]
            material.save()
            if include_groups:
                material.groups.set(form.cleaned_data["groups"])
            _audit(request, "material.update", material)
            if material.current_version_id:
                from .search import index_version

                index_version(material.current_version)
            messages.success(request, "资料信息已更新。")
            return redirect("material_detail", material_id=material.pk)
    return render(
        request,
        "library/edit.html",
        {"form": form, "material": material, "include_groups": include_groups},
    )


@login_required
@require_POST
def replace_material(request, material_id):
    material = get_object_or_404(accessible_materials(request.user), pk=material_id)
    if not can_manage_material(request.user, material):
        messages.error(request, "只有上传者或管理员可以替换文件。")
        return redirect("material_detail", material_id=material.pk)
    uploaded = request.FILES.get("file")
    if not uploaded:
        messages.error(request, "请选择要替换的新文件。")
    elif uploaded.size > settings.MAX_UPLOAD_SIZE:
        messages.error(request, "单个文件不能超过 100 MB。")
    else:
        version = MaterialVersion.objects.create(
            material=material,
            version_number=(material.versions.order_by("-version_number").values_list("version_number", flat=True).first() or 0) + 1,
            original_file=uploaded,
            original_name=Path(uploaded.name).name[:255],
            extension=Path(uploaded.name).suffix.lower()[:20],
            content_type=uploaded.content_type or "application/octet-stream",
            file_size=uploaded.size,
            created_by=request.user,
        )
        material.current_version = version
        material.save(update_fields=["current_version", "updated_at"])
        _audit(request, "material.replace", material, version=version.version_number)
        _enqueue_processing(version.pk)
        messages.success(request, "已接收新版本，旧版本仍保留。")
    return redirect("material_detail", material_id=material.pk)


@login_required
def download_material(request, material_id):
    material = get_object_or_404(accessible_materials(request.user), pk=material_id)
    version = material.current_version
    if not version or not version.original_file:
        raise Http404
    _audit(request, "material.download", material, version=version.version_number)
    response = FileResponse(
        version.original_file.open("rb"),
        as_attachment=True,
        filename=version.original_name,
        content_type="application/octet-stream",
    )
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
def preview_material(request, material_id):
    material = get_object_or_404(accessible_materials(request.user), pk=material_id)
    version = material.current_version
    if not version or not version.original_file:
        raise Http404
    extension = version.extension.lower()
    if extension == ".docx" and version.status == MaterialVersion.Status.READY and version.preview_file:
        file_field = version.preview_file
        filename = f"{Path(version.original_name).stem}.pdf"
        content_type = "application/pdf"
    elif extension == ".pdf" and version.status == MaterialVersion.Status.READY:
        with version.original_file.open("rb") as source:
            if not source.read(5).startswith(b"%PDF-"):
                raise Http404
        file_field = version.original_file
        filename = version.original_name
        content_type = "application/pdf"
    elif extension == ".png" and version.status == MaterialVersion.Status.READY and version.content_type == "image/png":
        file_field = version.original_file
        filename = version.original_name
        content_type = "image/png"
    else:
        messages.info(request, "该文件类型暂不支持在线预览，请下载查看。")
        return redirect("material_detail", material_id=material.pk)
    if not file_field:
        messages.info(request, "预览文件尚未生成或生成失败，可下载原件查看。")
        return redirect("material_detail", material_id=material.pk)
    _audit(request, "material.preview", material, version=version.version_number)
    response = FileResponse(file_field.open("rb"), content_type=content_type)
    response["Content-Disposition"] = content_disposition_header(False, filename)
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
    return response


@login_required
@require_POST
def delete_material(request, material_id):
    material = get_object_or_404(accessible_materials(request.user), pk=material_id)
    if not can_manage_material(request.user, material):
        messages.error(request, "只有上传者或管理员可以移入回收站。")
    else:
        material.deleted_at = timezone.now()
        material.deleted_by = request.user
        material.save(update_fields=["deleted_at", "deleted_by", "updated_at"])
        _audit(request, "material.delete", material)
        if material.current_version_id:
            from .search import index_version

            index_version(material.current_version)
        messages.success(request, "资料已移入回收站，管理员可随时恢复。")
    return redirect("home")


@login_required
def recycle_bin(request):
    if not (request.user.is_staff or request.user.is_superuser):
        raise Http404
    materials = accessible_materials(request.user, include_deleted=True).filter(deleted_at__isnull=False)
    return render(request, "library/recycle_bin.html", {"materials": materials})


@login_required
@require_POST
def restore_material(request, material_id):
    material = get_object_or_404(accessible_materials(request.user, include_deleted=True), pk=material_id, deleted_at__isnull=False)
    if not (request.user.is_staff or request.user.is_superuser or material.uploaded_by_id == request.user.id):
        raise Http404
    material.deleted_at = None
    material.deleted_by = None
    material.save(update_fields=["deleted_at", "deleted_by", "updated_at"])
    _audit(request, "material.restore", material)
    if material.current_version_id:
        from .search import index_version

        index_version(material.current_version)
    messages.success(request, "资料已恢复。")
    return redirect("recycle_bin" if request.user.is_staff or request.user.is_superuser else "home")


def health(request):
    return JsonResponse({"status": "ok"})


@login_required
def system_status(request):
    if not (request.user.is_staff or request.user.is_superuser):
        raise Http404
    from django.contrib.auth import get_user_model
    from django.db.models import Sum
    import requests

    root = settings.DATA_ROOT
    root.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(root)
    storage_bytes = MaterialVersion.objects.aggregate(total=Sum("file_size"))["total"] or 0
    try:
        response = requests.get(f"{settings.MEILI_URL}/health", timeout=2)
        search_available = response.ok
    except requests.RequestException:
        search_available = False
    context = {
        "disk_total": usage.total,
        "disk_used": usage.used,
        "disk_free": usage.free,
        "disk_percent": round((usage.used / usage.total) * 100) if usage.total else 0,
        "stored_bytes": storage_bytes,
        "material_count": Material.objects.filter(deleted_at__isnull=True).count(),
        "version_count": MaterialVersion.objects.count(),
        "pending_count": MaterialVersion.objects.filter(status__in=["pending", "processing"]).count(),
        "failed_count": MaterialVersion.objects.filter(status="failed").count(),
        "user_count": get_user_model().objects.filter(is_active=True).count(),
        "group_count": ArchiveGroup.objects.filter(is_active=True).count(),
        "search_available": search_available,
        "low_disk": usage.free < 2 * 1024 * 1024 * 1024,
    }
    return render(request, "library/system_status.html", context)

