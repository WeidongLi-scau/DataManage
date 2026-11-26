import logging
import shutil
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import content_disposition_header
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from PIL import Image

from .forms import LoginForm, MaterialCategoryForm, MaterialCollectionForm, MaterialMetadataForm, UploadMetadataForm
from .models import ArchiveGroup, AuditLog, Folder, Journal, Material, MaterialCategory, MaterialCollection, MaterialVersion, PublicationMetadata, UploadBatch, UploadBatchItem, UserAccountState
from .permissions import accessible_collections, accessible_groups, accessible_materials, can_manage_material
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
            account_state, _ = UserAccountState.objects.get_or_create(user=user, defaults={"must_change_password": user.last_login is None})
            login(request, user)
            AuditLog.objects.create(actor=user, action="login.success")
            if account_state.must_change_password:
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
    if not request.session.get("must_change_password") and not UserAccountState.objects.filter(user=request.user, must_change_password=True).exists():
        return redirect("home")
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            user = form.save()
            UserAccountState.objects.update_or_create(user=user, defaults={"must_change_password": False})
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
    from .services.folders import descendants, folder_choices, visible_folders

    query = request.GET.get("q", "").strip()[:200]
    results = search_materials(request.user, query)
    extension = request.GET.get("type", "").strip().lower().lstrip(".")
    category = request.GET.get("category", "").strip()[:120]
    group_id = request.GET.get("group", "").strip()
    folder_id = request.GET.get("folder", "").strip()
    selected_folder = None
    folder_error = ""
    folder_ids = set()
    if folder_id:
        allowed_folders = visible_folders(request.user)
        if group_id.isdigit():
            allowed_folders = allowed_folders.filter(group_id=int(group_id))
        selected_folder = allowed_folders.filter(pk=int(folder_id)).first() if folder_id.isdigit() else None
        if selected_folder:
            folder_ids = descendants(selected_folder)
        else:
            folder_error = "所选文件夹不属于当前项目或已不可访问，请重新选择。"
        if isinstance(results, list):
            results = [item for item in results if item.folder_id in folder_ids]
        else:
            results = results.filter(folder_id__in=folder_ids)
    uploader_id = request.GET.get("uploader", "").strip()
    uploaded_after = parse_date(request.GET.get("after", ""))
    uploaded_before = parse_date(request.GET.get("before", ""))
    document_after = parse_date(request.GET.get("document_after", ""))
    document_before = parse_date(request.GET.get("document_before", ""))
    collection_id = request.GET.get("collection", "").strip()
    record_type = request.GET.get("record_type", "").strip()
    author_filter = request.GET.get("author", "").strip()[:200]
    journal_filter = request.GET.get("journal", "").strip()[:200]
    year_filter = request.GET.get("year", "").strip()[:4]
    volume_filter = request.GET.get("volume", "").strip()[:80]
    issue_filter = request.GET.get("issue", "").strip()[:80]
    doi_filter = request.GET.get("doi", "").strip()[:255]
    sort = request.GET.get("sort", "-created_at")
    filters = ("-created_at", "created_at", "-document_date", "document_date")
    def item_document_date(item):
        if item.record_type == Material.RecordType.PUBLICATION and hasattr(item, "publication"):
            return item.publication.publication_date
        return item.document_date
    if isinstance(results, list):
        if extension:
            results = [item for item in results if item.extension.lstrip(".").lower() == extension]
        if category:
            results = [item for item in results if item.category_id == int(category)] if category.isdigit() else results
        if group_id.isdigit():
            results = [item for item in results if item.groups.filter(pk=int(group_id)).exists()]
        if uploader_id.isdigit():
            results = [item for item in results if item.uploaded_by_id == int(uploader_id)]
        if uploaded_after:
            results = [item for item in results if item.created_at.date() >= uploaded_after]
        if uploaded_before:
            results = [item for item in results if item.created_at.date() <= uploaded_before]
        if collection_id.isdigit():
            results = [item for item in results if item.collections.filter(pk=int(collection_id)).exists()]
        if record_type in dict(Material.RecordType.choices):
            results = [item for item in results if item.record_type == record_type]
        if document_after:
            results = [item for item in results if item_document_date(item) and item_document_date(item) >= document_after]
        if document_before:
            results = [item for item in results if item_document_date(item) and item_document_date(item) <= document_before]
        if author_filter:
            results = [item for item in results if item.record_type == Material.RecordType.PUBLICATION and hasattr(item, "publication") and author_filter.casefold() in item.publication.authors.casefold()]
        if journal_filter:
            results = [item for item in results if item.record_type == Material.RecordType.PUBLICATION and hasattr(item, "publication") and journal_filter.casefold() in item.publication.displayed_journal.casefold()]
        if year_filter.isdigit():
            results = [item for item in results if item.record_type == Material.RecordType.PUBLICATION and hasattr(item, "publication") and item.publication.publication_year == int(year_filter)]
        if volume_filter:
            results = [item for item in results if item.record_type == Material.RecordType.PUBLICATION and hasattr(item, "publication") and volume_filter.casefold() in item.publication.volume.casefold()]
        if issue_filter:
            results = [item for item in results if item.record_type == Material.RecordType.PUBLICATION and hasattr(item, "publication") and issue_filter.casefold() in item.publication.issue.casefold()]
        if doi_filter:
            results = [item for item in results if item.record_type == Material.RecordType.PUBLICATION and hasattr(item, "publication") and doi_filter.casefold() in item.publication.doi.casefold()]
        if sort in filters:
            reverse = sort.startswith("-")
            sort_field = sort.lstrip("-")
            results.sort(key=lambda item: (item_document_date(item) is None, item_document_date(item) or "") if sort_field == "document_date" else getattr(item, sort_field), reverse=reverse)
    else:
        if extension:
            results = results.filter(current_version__extension__iexact=f".{extension}")
        if category.isdigit():
            results = results.filter(category_id=int(category))
        if group_id.isdigit():
            results = results.filter(groups__id=int(group_id))
        if uploader_id.isdigit():
            results = results.filter(uploaded_by_id=int(uploader_id))
        if uploaded_after:
            results = results.filter(created_at__date__gte=uploaded_after)
        if uploaded_before:
            results = results.filter(created_at__date__lte=uploaded_before)
        if collection_id.isdigit():
            results = results.filter(collections__id=int(collection_id))
        if record_type in dict(Material.RecordType.choices):
            results = results.filter(record_type=record_type)
        if document_after:
            results = results.filter(Q(document_date__gte=document_after) | Q(publication__publication_date__gte=document_after))
        if document_before:
            results = results.filter(Q(document_date__lte=document_before) | Q(publication__publication_date__lte=document_before))
        if author_filter:
            results = results.filter(publication__authors__icontains=author_filter)
        if journal_filter:
            results = results.filter(Q(publication__journal_name__icontains=journal_filter) | Q(publication__journal__name__icontains=journal_filter))
        if year_filter.isdigit():
            results = results.filter(Q(publication__publication_year=int(year_filter)) | Q(publication__publication_date__year=int(year_filter)))
        if volume_filter:
            results = results.filter(publication__volume__icontains=volume_filter)
        if issue_filter:
            results = results.filter(publication__issue__icontains=issue_filter)
        if doi_filter:
            results = results.filter(publication__doi__icontains=doi_filter)
        if sort in filters:
            if sort.lstrip("-") == "document_date":
                from django.db.models.functions import Coalesce

                results = results.annotate(effective_date=Coalesce("document_date", "publication__publication_date")).order_by(f"{'-' if sort.startswith('-') else ''}effective_date", "-created_at")
            else:
                results = results.order_by(sort)
        results = results.distinct()
    paginator = Paginator(results, 20)
    page_obj = paginator.get_page(request.GET.get("page"))
    pagination_params = request.GET.copy()
    pagination_params.pop("page", None)
    context = {
        "page_obj": page_obj,
        "has_filters": any(request.GET.get(key) for key in ("q", "type", "category", "group", "folder", "uploader", "after", "before", "document_after", "document_before", "collection", "record_type", "author", "journal", "year", "volume", "issue", "doi")),
        "query": query,
        "type_filter": extension,
        "category_filter": category,
        "group_filter": group_id,
        "folder_filter": folder_id,
        "folder_error": folder_error,
        "selected_folder": selected_folder,
        "folder_options": folder_choices(request.user),
        "uploader_filter": uploader_id,
        "uploaded_after": request.GET.get("after", ""),
        "uploaded_before": request.GET.get("before", ""),
        "document_after": request.GET.get("document_after", ""),
        "document_before": request.GET.get("document_before", ""),
        "collection_filter": collection_id,
        "record_type_filter": record_type,
        "sort_filter": sort,
        "author_filter": author_filter,
        "journal_filter": journal_filter,
        "year_filter": year_filter,
        "volume_filter": volume_filter,
        "issue_filter": issue_filter,
        "doi_filter": doi_filter,
        "pagination_query": pagination_params.urlencode(),
        "groups": _group_queryset(request.user),
        "collections": accessible_collections(request.user),
        "categories": MaterialCategory.objects.filter(is_active=True),
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
    import json
    from django.core.exceptions import ValidationError
    from .services.folders import can_create, folder_path, resolve_upload_folder, upload_parts, visible_folders
    progress_mode = request.method == "POST" and request.POST.get("response_mode") == "progress"
    progress_batch = None
    if progress_mode and request.POST.get("progress_batch"):
        progress_batch = get_object_or_404(UploadBatch, pk=request.POST["progress_batch"], created_by=request.user) if request.POST["progress_batch"].isdigit() else None
        if progress_batch is None:
            return JsonResponse({"error": "上传批次无效，请重新打开上传页。"}, status=400)
    folder_id = request.POST.get("folder") if request.method == "POST" else request.GET.get("folder")
    if folder_id and not folder_id.isdigit():
        raise Http404
    selected_folder = get_object_or_404(visible_folders(request.user), pk=folder_id) if folder_id and folder_id.isdigit() else None
    if selected_folder and not can_create(request.user, selected_folder.group):
        raise Http404
    allowed_groups = _group_queryset(request.user)
    allowed_collections = accessible_collections(request.user).filter(groups__in=allowed_groups).distinct()
    form = UploadMetadataForm(
        request.POST if request.method == "POST" else None,
        group_queryset=allowed_groups,
        collection_queryset=allowed_collections,
    )
    if request.method == "GET":
        private_groups = list(allowed_groups.filter(is_public=False)[:2])
        if selected_folder:
            form.initial["groups"] = [selected_folder.group_id]
        elif len(private_groups) == 1:
            form.initial["groups"] = [private_groups[0].pk]
    if request.method == "POST":
        files = request.FILES.getlist("files")
        if progress_mode and len(files) != 1:
            return JsonResponse({"error": "进度上传每次只接收一个文件。"}, status=400)
        relative_paths = []
        form.is_valid()
        try:
            raw_paths = json.loads(request.POST.get("relative_paths", "[]"))
            if not isinstance(raw_paths, list) or (raw_paths and len(raw_paths) != len(files)) or any(not isinstance(path, str) for path in raw_paths):
                raise ValidationError("上传文件与目录路径数量不一致，请重新选择文件。")
            relative_paths = [upload_parts(path) for path in raw_paths] if raw_paths else [[] for _ in files]
            if any(parts and parts[-1] != uploaded.name for parts, uploaded in zip(relative_paths, files)):
                raise ValidationError("文件名与目录路径不一致，请重新选择文件。")
        except (ValueError, ValidationError) as exc:
            form.add_error(None, "；".join(exc.messages) if isinstance(exc, ValidationError) else "上传路径格式无效。")
        if not files:
            form.add_error(None, "请选择至少一个文件。")
        if form.is_valid() and files:
            if not form.cleaned_data["groups"]:
                form.add_error("groups", "请至少选择一个可见分组。")
            selected_group_ids = set(form.cleaned_data["groups"].values_list("pk", flat=True))
            for collection in form.cleaned_data["collections"]:
                if not collection.groups.filter(pk__in=selected_group_ids).exists():
                    form.add_error("collections", f"资料集合“{collection.name}”与所选项目组不匹配。")
            oversized = [item.name for item in files if item.size > settings.MAX_UPLOAD_SIZE]
            if oversized:
                form.add_error(None, f"以下文件超过 100 MB 上限：{', '.join(oversized[:5])}")
            max_batch_size = getattr(settings, "MAX_BATCH_UPLOAD_SIZE", 1024 * 1024 * 1024)
            if sum(item.size for item in files) > max_batch_size:
                form.add_error(None, "本次批量上传总量超过 1 GB 上限，请分批上传。")
            if progress_batch:
                from django.db.models import Sum
                saved_size = progress_batch.items.aggregate(total=Sum("material__current_version__file_size"))["total"] or 0
                if saved_size + sum(item.size for item in files) > max_batch_size:
                    form.add_error(None, "本批已保存文件与当前文件合计超过 1 GB，请另开一个批次。")
        if form.is_valid() and files and not form.errors:
            if selected_folder and set(form.cleaned_data["groups"].values_list("pk", flat=True)) != {selected_folder.group_id}:
                form.add_error("groups", "目录上传只能使用该目录所属项目的授权。")
            if selected_folder is None:
                primary = form.cleaned_data["groups"].order_by("is_public", "pk").first()
                selected_folder, _ = Folder.objects.get_or_create(group=primary, parent=None, normalized_name="待整理资料", deleted_at=None, defaults={"name": "待整理资料", "created_by": request.user})
        if form.is_valid() and files and not form.errors:
            batch = progress_batch or UploadBatch.objects.create(created_by=request.user, title=f"批量上传 {timezone.localtime():%Y-%m-%d %H:%M}")
            failed_count = 0
            for file_index, uploaded in enumerate(files):
                clean_name = Path(uploaded.name).name[:255]
                prefix = f"file_{file_index}"
                file_category_id = request.POST.get(f"{prefix}_category", "")
                file_category = MaterialCategory.objects.filter(pk=file_category_id, is_active=True).first() if file_category_id.isdigit() else form.cleaned_data["category"]
                item_values = {
                    "title": request.POST.get(f"{prefix}_title", form.cleaned_data["title"]).strip() or Path(clean_name).stem[:255],
                    "description": request.POST.get(f"{prefix}_description", "").strip() or form.cleaned_data["description"],
                    "category": file_category,
                    "tags": request.POST.get(f"{prefix}_tags", "").strip() or form.cleaned_data["tags"],
                    "record_type": form.cleaned_data["record_type"],
                    "document_date": parse_date(request.POST.get(f"{prefix}_document_date", "")) or form.cleaned_data["document_date"],
                    "journal": form.cleaned_data["journal"],
                    "journal_name": form.cleaned_data["journal_name"],
                    "authors": form.cleaned_data["authors"],
                    "publication_date": form.cleaned_data["publication_date"],
                    "publication_year": form.cleaned_data["publication_year"],
                    "publication_month": form.cleaned_data["publication_month"],
                    "bibtex_raw": form.cleaned_data["bibtex_raw"],
                    "volume": form.cleaned_data["volume"],
                    "issue": form.cleaned_data["issue"],
                    "pages": form.cleaned_data["pages"],
                    "doi": form.cleaned_data["doi"],
                    "url": form.cleaned_data["url"],
                }
                try:
                    with transaction.atomic():
                        material = Material.objects.create(
                            title=item_values["title"],
                            description=item_values["description"],
                            category=item_values["category"],
                            tags=item_values["tags"],
                            record_type=item_values["record_type"],
                            document_date=item_values["document_date"] if item_values["record_type"] == Material.RecordType.MATERIAL else None,
                            uploaded_by=request.user,
                            upload_batch=batch,
                            folder=resolve_upload_folder(selected_folder, relative_paths[file_index], request.user),
                        )
                        material.groups.set(form.cleaned_data["groups"])
                        material.collections.set(form.cleaned_data["collections"])
                        from .services.uploads import create_version

                        version = create_version(material=material, uploaded_file=uploaded, user=request.user, version_number=1)
                        material.current_version = version
                        material.save(update_fields=["current_version", "updated_at"])
                        if form.cleaned_data["record_type"] == Material.RecordType.PUBLICATION:
                            _save_publication(material, item_values)
                        batch_item = UploadBatchItem.objects.create(batch=batch, material=material, original_name=version.original_name)
                        _audit(request, "material.upload", material, file_name=clean_name, size=uploaded.size, batch=batch.pk)
                        transaction.on_commit(lambda version_id=version.pk: _enqueue_processing(version_id))
                except Exception as exc:
                    logger.exception("Could not save uploaded file %s in batch %s", clean_name, batch.pk)
                    batch_item = UploadBatchItem.objects.create(
                        batch=batch,
                        original_name=clean_name,
                        status=MaterialVersion.Status.FAILED,
                        error_message="资料记录创建失败；请检查文件后重试。",
                    )
                    _audit(request, "material.upload.failure", batch, file_name=clean_name, error=type(exc).__name__)
                    failed_count += 1
            success_count = len(files) - failed_count
            if progress_mode:
                batch_item.refresh_from_db()
                return JsonResponse({
                    "saved": bool(batch_item.material_id), "batch_id": batch.pk,
                    "batch_url": reverse("upload_batch_detail", args=[batch.pk]),
                    "material_url": reverse("material_detail", args=[batch_item.material_id]) if batch_item.material_id else None,
                    "status": batch_item.status, "status_label": batch_item.get_status_display(),
                    "message": batch_item.error_message,
                })
            messages.success(request, f"批量上传已处理：{success_count} 个成功，{failed_count} 个失败。")
            return redirect("upload_batch_detail", batch_id=batch.pk)
        if progress_mode:
            return JsonResponse({"error": "请检查填写的信息。", "errors": {name: [str(error) for error in errors] for name, errors in form.errors.items()}}, status=422)
    return render(request, "library/upload.html", {
        "form": form,
        "categories": MaterialCategory.objects.filter(is_active=True),
        "journals": Journal.objects.filter(is_active=True),
        "max_size_mb": settings.MAX_UPLOAD_SIZE // (1024 * 1024),
        "selected_folder": selected_folder,
        "folder_choices": [(f.pk, folder_path(f), f.group_id) for f in visible_folders(request.user)],
    })


def _enqueue_processing(version_id):
    if settings.DEV_SYNC_FILE_PROCESSING:
        from .tasks import process_version

        try:
            process_version(version_id)
        except Exception:
            logger.exception("Could not process material version %s synchronously", version_id)
            version = MaterialVersion.objects.get(pk=version_id)
            version.status = MaterialVersion.Status.FAILED
            version.error_message = "本地文件处理失败；原件已保存，可重试。"
            version.save(update_fields=["status", "error_message"])
            UploadBatchItem.objects.filter(material=version.material).update(status=version.status, error_message=version.error_message)
        return
    from django_rq import enqueue

    try:
        enqueue("library.tasks.process_version", version_id, job_timeout=600)
    except Exception:
        logger.exception("Could not enqueue material processing job %s", version_id)
        version = MaterialVersion.objects.get(pk=version_id)
        version.status = MaterialVersion.Status.FAILED
        version.error_message = "后台处理服务暂不可用；原文件已保存，可稍后重试。"
        version.save(update_fields=["status", "error_message"])
        if hasattr(version.material, "batch_item"):
            UploadBatchItem.objects.filter(material=version.material).update(status=version.status, error_message=version.error_message)
        from .search import index_version

        index_version(version)


def _save_publication(material, values):
    journal = values.get("journal")
    journal_name = values.get("journal_name", "").strip()
    if not journal and journal_name:
        normalized_name = " ".join(journal_name.split()).casefold()
        journal = Journal.objects.filter(Q(normalized_name=normalized_name) | Q(abbreviation__iexact=journal_name), is_active=True).first()
    if journal and not journal_name:
        journal_name = journal.name
    publication_date = values.get("publication_date")
    publication_year = values.get("publication_year") or (publication_date.year if publication_date else None)
    publication_month = values.get("publication_month") or (publication_date.month if publication_date else None)
    publication, _ = PublicationMetadata.objects.update_or_create(
        material=material,
        defaults={
            "bibtex_raw": values.get("bibtex_raw", ""),
            "authors": values.get("authors", ""),
            "publication_date": values.get("publication_date"),
            "publication_year": publication_date.year if publication_date else publication_year,
            "publication_month": publication_date.month if publication_date else publication_month,
            "journal": journal,
            "journal_name": journal_name,
            "volume": values.get("volume", ""),
            "issue": values.get("issue", ""),
            "pages": values.get("pages", ""),
            "doi": values.get("doi", ""),
            "url": values.get("url", ""),
        },
    )
    return publication


@login_required
def upload_batch_detail(request, batch_id):
    batch = get_object_or_404(UploadBatch.objects.prefetch_related("items__material"), pk=batch_id)
    if not (request.user.is_staff or request.user.is_superuser or batch.created_by_id == request.user.id):
        raise Http404
    return render(request, "library/upload_batch.html", {"batch": batch})


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
        {"material": material, "versions": material.versions.select_related("created_by").all(), "can_manage": can_manage_material(request.user, material)},
    )


@login_required
def edit_material(request, material_id):
    material = get_object_or_404(accessible_materials(request.user), pk=material_id)
    if not can_manage_material(request.user, material):
        messages.error(request, "只有上传者或管理员可以编辑该资料。")
        return redirect("material_detail", material_id=material.pk)
    include_groups = True
    allowed_groups = ArchiveGroup.objects.filter(is_active=True) if request.user.is_staff or request.user.is_superuser else _group_queryset(request.user)
    allowed_collections = accessible_collections(request.user).filter(groups__in=allowed_groups).distinct()
    preserved_groups = list(material.groups.exclude(pk__in=allowed_groups))
    preserved_collections = list(material.collections.exclude(pk__in=allowed_collections))
    form = MaterialMetadataForm(
        request.POST or None,
        group_queryset=allowed_groups,
        collection_queryset=allowed_collections,
        include_groups=include_groups,
        initial={
            "title": material.title,
            "description": material.description,
            "category": material.category_id,
            "tags": material.tags,
            "groups": material.groups.filter(pk__in=allowed_groups),
            "collections": material.collections.filter(pk__in=allowed_collections),
            "record_type": material.record_type,
            "document_date": material.document_date,
            "bibtex_raw": getattr(getattr(material, "publication", None), "bibtex_raw", ""),
            "authors": getattr(getattr(material, "publication", None), "authors", ""),
            "publication_date": getattr(getattr(material, "publication", None), "publication_date", None),
            "publication_year": getattr(getattr(material, "publication", None), "publication_year", None),
            "publication_month": getattr(getattr(material, "publication", None), "publication_month", None),
            "journal": getattr(getattr(material, "publication", None), "journal_id", None),
            "journal_name": getattr(getattr(material, "publication", None), "displayed_journal", ""),
            "volume": getattr(getattr(material, "publication", None), "volume", ""),
            "issue": getattr(getattr(material, "publication", None), "issue", ""),
            "pages": getattr(getattr(material, "publication", None), "pages", ""),
            "doi": getattr(getattr(material, "publication", None), "doi", ""),
            "url": getattr(getattr(material, "publication", None), "url", ""),
        },
    )
    if request.method == "POST" and form.is_valid():
        selected_groups = list(form.cleaned_data["groups"]) + preserved_groups
        selected_collections = list(form.cleaned_data["collections"]) + preserved_collections
        selected_group_ids = {group.pk for group in selected_groups}
        invalid_collection = any(not collection.groups.filter(pk__in=selected_group_ids).exists() for collection in selected_collections)
        if not selected_groups:
            form.add_error("groups", "资料至少需要归属一个分组。")
        elif material.folder_id and material.folder.group_id not in selected_group_ids:
            form.add_error("groups", "所属目录的项目授权不可在此移除；请使用跨项目移动调整归档与授权。")
        elif invalid_collection:
            form.add_error("collections", "所选资料集合不属于资料的任一授权分组。")
        else:
            material.title = form.cleaned_data["title"]
            material.description = form.cleaned_data["description"]
            material.category = form.cleaned_data["category"]
            material.tags = form.cleaned_data["tags"]
            material.record_type = form.cleaned_data["record_type"]
            material.document_date = form.cleaned_data["document_date"] if material.record_type == Material.RecordType.MATERIAL else None
            material.save()
            material.groups.set(selected_groups)
            material.collections.set(selected_collections)
            if material.record_type == Material.RecordType.PUBLICATION:
                _save_publication(material, form.cleaned_data)
            _audit(request, "material.update", material)
            if material.current_version_id:
                from .search import index_version

                index_version(material.current_version)
            messages.success(request, "资料信息已更新。")
            return redirect("material_detail", material_id=material.pk)
    return render(
        request,
        "library/edit.html",
        {"form": form, "material": material, "include_groups": include_groups, "journals": Journal.objects.filter(is_active=True)},
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
        from .services.uploads import create_version

        with transaction.atomic():
            material = Material.objects.select_for_update().get(pk=material.pk)
            version = create_version(
                material=material,
                uploaded_file=uploaded,
                user=request.user,
                version_number=(material.versions.order_by("-version_number").values_list("version_number", flat=True).first() or 0) + 1,
            )
            material.current_version = version
            material.save(update_fields=["current_version", "updated_at"])
            _audit(request, "material.replace", material, version=version.version_number)
            transaction.on_commit(lambda: _enqueue_processing(version.pk))
        messages.success(request, "已接收新版本，旧版本仍保留。")
    return redirect("material_detail", material_id=material.pk)


@login_required
def download_material(request, material_id, version_id=None):
    material = get_object_or_404(accessible_materials(request.user), pk=material_id)
    version = get_object_or_404(material.versions, pk=version_id) if version_id else material.current_version
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
def preview_material(request, material_id, version_id=None):
    material = get_object_or_404(accessible_materials(request.user), pk=material_id)
    version = get_object_or_404(material.versions, pk=version_id) if version_id else material.current_version
    try:
        file_field, filename, content_type = _preview_asset(version)
    except (Http404, OSError, ValueError):
        messages.info(request, "该文件类型暂不支持在线预览，请下载查看。")
        return redirect("material_detail", material_id=material.pk)
    _audit(request, "material.preview", material, version=version.version_number)
    return render(request, "library/preview.html", {
        "material": material,
        "preview_name": filename,
        "preview_type": content_type,
        "preview_url": reverse("version_preview_content", args=[material.pk, version.pk]) if version_id else reverse("preview_content", args=[material.pk]),
        "preview_version": version,
        "download_url": reverse("version_download", args=[material.pk, version.pk]) if version_id else reverse("download_material", args=[material.pk]),
    })


def _preview_asset(version):
    if not version or not version.original_file:
        raise Http404
    extension = version.extension.lower()
    if extension == ".pdf":
        file_field = version.original_file
        filename = version.original_name
    elif extension == ".docx" and version.preview_file:
        file_field = version.preview_file
        filename = f"{Path(version.original_name).stem}.pdf"
    elif extension == ".png":
        file_field = version.original_file
        filename = version.original_name
    else:
        raise Http404

    with file_field.open("rb") as source:
        signature = source.read(8)
        source.seek(0)
        if extension == ".png":
            if signature != b"\x89PNG\r\n\x1a\n":
                raise Http404
            with Image.open(source) as image:
                image.verify()
            return file_field, filename, "image/png"
        if not signature.startswith(b"%PDF-"):
            raise Http404
    return file_field, filename, "application/pdf"


@login_required
def preview_content(request, material_id, version_id=None):
    material = get_object_or_404(accessible_materials(request.user), pk=material_id)
    version = get_object_or_404(material.versions, pk=version_id) if version_id else material.current_version
    try:
        file_field, filename, content_type = _preview_asset(version)
    except (Http404, OSError, ValueError):
        raise Http404
    _audit(request, "material.preview.content", material, version=version.version_number)
    response = FileResponse(file_field.open("rb"), content_type=content_type)
    response["Content-Disposition"] = content_disposition_header(False, filename)
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'self'; object-src 'none'; base-uri 'none'"
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
    folders = Folder.objects.filter(deleted_at__isnull=False).select_related("group", "parent")
    folders = [folder for folder in folders if not folder.parent or folder.parent.deletion_batch != folder.deletion_batch]
    return render(request, "library/recycle_bin.html", {"materials": materials, "folders": folders})


@login_required
@require_POST
def restore_material(request, material_id):
    material = get_object_or_404(accessible_materials(request.user, include_deleted=True), pk=material_id, deleted_at__isnull=False)
    if material.folder and material.folder.deleted_at:
        messages.error(request, "请先从回收站恢复所属文件夹。")
        return redirect("recycle_bin")
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
@require_POST
def parse_bibtex(request):
    from .services.bibliography import parse_bibtex as parse_entry

    try:
        values = parse_entry(request.POST.get("bibtex", ""))
    except Exception:
        return JsonResponse({"error": "无法识别这段 BibTeX；可保留原文并手动填写书目信息。"}, status=400)
    journal_name = values.get("journal_name", "")
    normalized_journal = " ".join(journal_name.split()).casefold()
    journal = Journal.objects.filter(
        (Q(normalized_name=normalized_journal) | Q(abbreviation__iexact=journal_name)) & Q(is_active=True)
    ).first() if normalized_journal else None
    values["journal_id"] = journal.pk if journal else None
    values["journal_name"] = journal.name if journal else journal_name
    for field in ("publication_date",):
        if values[field]:
            values[field] = values[field].isoformat()
    return JsonResponse(values)


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
    from .models import StoredFile

    storage_bytes = StoredFile.objects.aggregate(total=Sum("file_size"))["total"] or 0
    if settings.MEILI_ENABLED:
        try:
            response = requests.get(f"{settings.MEILI_URL}/health", timeout=2)
            search_available = response.ok
        except requests.RequestException:
            search_available = False
    else:
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

