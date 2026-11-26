from functools import wraps
import secrets

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from django.views.decorators.cache import never_cache

from .forms import ArchiveGroupForm, JournalForm, ManagedUserCreateForm, ManagedUserEditForm, MaterialCategoryForm, MaterialCollectionForm
from .models import ArchiveGroup, AuditLog, Journal, MaterialCategory, MaterialCollection, UserAccountState
from .permissions import can_manage_collection, manageable_groups


def staff_view(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("login")
        if not (request.user.is_staff or request.user.is_superuser):
            raise Http404
        return view(request, *args, **kwargs)

    return wrapped


def collection_manager_view(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("login")
        return view(request, *args, **kwargs)

    return wrapped


@staff_view
def management_home(request):
    context = {
        "user_count": get_user_model().objects.filter(is_active=True).count(),
        "group_count": ArchiveGroup.objects.filter(is_active=True).count(),
        "collection_count": MaterialCollection.objects.filter(is_active=True).count(),
        "category_count": MaterialCategory.objects.filter(is_active=True).count(),
        "journal_count": Journal.objects.filter(is_active=True).count(),
    }
    return render(request, "library/manage_home.html", context)


@staff_view
@never_cache
@require_http_methods(["GET", "POST"])
def manage_users(request, user_id=None):
    user_model = get_user_model()
    user = get_object_or_404(user_model, pk=user_id) if user_id else None
    groups = ArchiveGroup.objects.all()
    credentials = None
    if request.method == "POST":
        form = ManagedUserEditForm(request.POST, instance=user, group_queryset=groups) if user else ManagedUserCreateForm(request.POST, group_queryset=groups)
        if form.is_valid():
            with transaction.atomic():
                if user_id:
                    user = form.save()
                else:
                    user = form.save(commit=False)
                    initial_password = secrets.token_urlsafe(18)
                    user.set_password(initial_password)
                    user.save()
                    user.archive_groups.set(form.cleaned_data["archive_groups"])
                    UserAccountState.objects.create(user=user)
                    credentials = {"username": user.username, "password": initial_password}
                AuditLog.objects.create(actor=request.user, action="account.update" if user_id else "account.create", object_type="User", object_id=str(user.pk))
            if user_id:
                messages.success(request, f"成员账户“{user.username}”已保存。")
                return redirect("manage_users")
            user = None
            form = ManagedUserCreateForm(group_queryset=groups)
    else:
        form = ManagedUserEditForm(instance=user, group_queryset=groups) if user else ManagedUserCreateForm(group_queryset=groups)
    return render(request, "library/manage_users.html", {"form": form, "users": user_model.objects.order_by("username"), "editing": user, "credentials": credentials})


@staff_view
@require_http_methods(["GET", "POST"])
def manage_groups(request, group_id=None):
    group = get_object_or_404(ArchiveGroup, pk=group_id) if group_id else None
    if group and group.is_public and request.method == "POST":
        messages.error(request, "公共资料分组的成员关系由系统维护。")
        return redirect("manage_groups")
    form = ArchiveGroupForm(request.POST or None, instance=group)
    if request.method == "POST" and form.is_valid():
        item = form.save()
        AuditLog.objects.create(actor=request.user, action="group.update" if group else "group.create", object_type="ArchiveGroup", object_id=str(item.pk))
        messages.success(request, "项目/课题分组已保存。")
        return redirect("manage_groups")
    return render(request, "library/manage_groups.html", {"form": form, "groups": ArchiveGroup.objects.order_by("name"), "editing": group})


@collection_manager_view
@require_http_methods(["GET", "POST"])
def manage_collections(request, collection_id=None):
    groups = manageable_groups(request.user)
    collection = None
    if collection_id:
        collection = get_object_or_404(MaterialCollection, pk=collection_id)
        if not can_manage_collection(request.user, collection):
            raise Http404
    form = MaterialCollectionForm(
        request.POST or None,
        group_queryset=groups if not collection else groups | collection.groups.filter(managers=request.user),
        initial={"groups": collection.groups.all()} if collection else None,
    )
    if request.method == "POST" and form.is_valid():
        if not form.cleaned_data["groups"]:
            form.add_error("groups", "集合至少需要关联一个项目/课题组。")
        else:
            item = collection or MaterialCollection(created_by=request.user)
            item.name = form.cleaned_data["name"]
            item.description = form.cleaned_data["description"]
            item.save()
            selected_groups = form.cleaned_data["groups"]
            if collection:
                unmanaged_groups = collection.groups.exclude(managers=request.user)
                item.groups.set(list(selected_groups) + list(unmanaged_groups))
            else:
                item.groups.set(selected_groups)
            AuditLog.objects.create(actor=request.user, action="collection.update" if collection else "collection.create", object_type="MaterialCollection", object_id=str(item.pk))
            messages.success(request, "资料集合已保存。")
            return redirect("manage_collections")
    collections = MaterialCollection.objects.filter(groups__in=groups).distinct().prefetch_related("groups")
    return render(request, "library/manage_collections.html", {"form": form, "collections": collections, "editing": collection})


@staff_view
@require_http_methods(["GET", "POST"])
def manage_categories(request, category_id=None):
    category = get_object_or_404(MaterialCategory, pk=category_id) if category_id else None
    form = MaterialCategoryForm(request.POST or None, instance=category)
    if request.method == "POST" and form.is_valid():
        item = form.save()
        AuditLog.objects.create(actor=request.user, action="category.update" if category else "category.create", object_type="MaterialCategory", object_id=str(item.pk))
        messages.success(request, "资料分类已保存。")
        return redirect("manage_categories")
    return render(request, "library/manage_categories.html", {"form": form, "categories": MaterialCategory.objects.order_by("name"), "editing": category})


@staff_view
@require_http_methods(["GET", "POST"])
def manage_journals(request, journal_id=None):
    journal = get_object_or_404(Journal, pk=journal_id) if journal_id else None
    form = JournalForm(request.POST or None, instance=journal)
    if request.method == "POST" and form.is_valid():
        item = form.save()
        AuditLog.objects.create(actor=request.user, action="journal.update" if journal else "journal.create", object_type="Journal", object_id=str(item.pk))
        messages.success(request, "期刊目录已保存。")
        return redirect("manage_journals")
    return render(request, "library/manage_journals.html", {"form": form, "journals": Journal.objects.order_by("name"), "editing": journal})
