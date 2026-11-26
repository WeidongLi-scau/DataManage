from django import forms
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.forms import UsernameField

from .models import ArchiveGroup, Journal, MaterialCategory, MaterialCollection


class LoginForm(forms.Form):
    username = forms.CharField(label="用户名", max_length=150)
    password = forms.CharField(label="密码", strip=False, widget=forms.PasswordInput)


class MaterialFieldsMixin:
    def configure_material_fields(self, *, group_queryset, collection_queryset, include_groups=True):
        self.fields["category"].queryset = MaterialCategory.objects.filter(is_active=True)
        self.fields["groups"].queryset = group_queryset
        self.fields["collections"].queryset = collection_queryset
        self.fields["journal"].queryset = Journal.objects.filter(is_active=True)
        if not include_groups:
            self.fields.pop("groups")


class UploadMetadataForm(MaterialFieldsMixin, forms.Form):
    record_type = forms.ChoiceField(label="资料类型", choices=(("material", "一般资料"), ("publication", "已发表成果")))
    title = forms.CharField(label="标题（选择文件后可逐个编辑）", max_length=255, required=False, widget=forms.Textarea(attrs={"rows": 2}))
    description = forms.CharField(label="资料说明", required=False, widget=forms.Textarea(attrs={"rows": 3}))
    category = forms.ModelChoiceField(label="分类", queryset=MaterialCategory.objects.none(), required=False, empty_label="未分类")
    tags = forms.CharField(label="标签（逗号分隔）", max_length=500, required=False)
    document_date = forms.DateField(label="资料日期", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    groups = forms.ModelMultipleChoiceField(label="所属项目/课题组", queryset=ArchiveGroup.objects.none(), widget=forms.CheckboxSelectMultiple, error_messages={"required": "请至少选择一个所属项目/课题组。"})
    collections = forms.ModelMultipleChoiceField(label="资料集合", queryset=MaterialCollection.objects.none(), required=False, widget=forms.CheckboxSelectMultiple)
    bibtex_raw = forms.CharField(label="BibTeX", required=False, widget=forms.Textarea(attrs={"rows": 5}))
    authors = forms.CharField(label="作者", required=False, widget=forms.Textarea(attrs={"rows": 2}))
    publication_date = forms.DateField(label="发表日期", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    publication_year = forms.IntegerField(label="发表年份", required=False, min_value=1500, max_value=2200)
    publication_month = forms.TypedChoiceField(
        label="发表月份",
        choices=[("", "未填写")] + [(str(month), f"{month:02d} 月") for month in range(1, 13)],
        coerce=lambda value: int(value) if value else None,
        required=False,
    )
    journal = forms.ModelChoiceField(label="期刊目录匹配项", queryset=Journal.objects.none(), required=False, widget=forms.HiddenInput)
    journal_name = forms.CharField(
        label="期刊 / 来源名称",
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"list": "journal-options", "autocomplete": "off", "data-journal-name": ""}),
    )
    volume = forms.CharField(label="卷", max_length=80, required=False)
    issue = forms.CharField(label="期", max_length=80, required=False)
    pages = forms.CharField(label="页码", max_length=120, required=False)
    doi = forms.CharField(label="DOI", max_length=255, required=False)
    url = forms.URLField(label="成果链接", max_length=500, required=False)

    def __init__(self, *args, group_queryset=None, collection_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.configure_material_fields(
            group_queryset=group_queryset or ArchiveGroup.objects.none(),
            collection_queryset=collection_queryset or MaterialCollection.objects.none(),
        )


class MaterialMetadataForm(MaterialFieldsMixin, forms.Form):
    record_type = forms.ChoiceField(label="资料类型", choices=(("material", "一般资料"), ("publication", "已发表成果")))
    title = forms.CharField(label="标题", max_length=255)
    description = forms.CharField(label="资料说明", required=False, widget=forms.Textarea(attrs={"rows": 4}))
    category = forms.ModelChoiceField(label="分类", queryset=MaterialCategory.objects.none(), required=False, empty_label="未分类")
    tags = forms.CharField(label="标签（逗号分隔）", max_length=500, required=False)
    document_date = forms.DateField(label="资料日期", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    groups = forms.ModelMultipleChoiceField(label="所属项目/课题组", queryset=ArchiveGroup.objects.none(), widget=forms.CheckboxSelectMultiple, required=False)
    collections = forms.ModelMultipleChoiceField(label="资料集合", queryset=MaterialCollection.objects.none(), required=False, widget=forms.CheckboxSelectMultiple)
    bibtex_raw = forms.CharField(label="BibTeX", required=False, widget=forms.Textarea(attrs={"rows": 5}))
    authors = forms.CharField(label="作者", required=False, widget=forms.Textarea(attrs={"rows": 2}))
    publication_date = forms.DateField(label="发表日期", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    publication_year = forms.IntegerField(label="发表年份", required=False, min_value=1500, max_value=2200)
    publication_month = forms.TypedChoiceField(
        label="发表月份",
        choices=[("", "未填写")] + [(str(month), f"{month:02d} 月") for month in range(1, 13)],
        coerce=lambda value: int(value) if value else None,
        required=False,
    )
    journal = forms.ModelChoiceField(label="期刊目录匹配项", queryset=Journal.objects.none(), required=False, widget=forms.HiddenInput)
    journal_name = forms.CharField(
        label="期刊 / 来源名称",
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"list": "journal-options", "autocomplete": "off", "data-journal-name": ""}),
    )
    volume = forms.CharField(label="卷", max_length=80, required=False)
    issue = forms.CharField(label="期", max_length=80, required=False)
    pages = forms.CharField(label="页码", max_length=120, required=False)
    doi = forms.CharField(label="DOI", max_length=255, required=False)
    url = forms.URLField(label="成果链接", max_length=500, required=False)

    def __init__(self, *args, group_queryset=None, collection_queryset=None, include_groups=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.configure_material_fields(
            group_queryset=group_queryset or ArchiveGroup.objects.none(),
            collection_queryset=collection_queryset or MaterialCollection.objects.none(),
            include_groups=include_groups,
        )


class MaterialCollectionForm(forms.Form):
    name = forms.CharField(label="集合名称", max_length=160)
    description = forms.CharField(label="集合说明", max_length=500, required=False, widget=forms.Textarea(attrs={"rows": 3}))
    groups = forms.ModelMultipleChoiceField(label="所属项目/课题组", queryset=ArchiveGroup.objects.none(), widget=forms.CheckboxSelectMultiple)

    def __init__(self, *args, group_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["groups"].queryset = group_queryset or ArchiveGroup.objects.none()


class MaterialCategoryForm(forms.ModelForm):
    class Meta:
        model = MaterialCategory
        fields = ["name", "is_active"]
        labels = {"name": "分类名称", "is_active": "启用"}


class JournalForm(forms.ModelForm):
    class Meta:
        model = Journal
        fields = ["name", "abbreviation", "is_active"]
        labels = {"name": "期刊名称", "abbreviation": "缩写", "is_active": "启用"}

    def clean_name(self):
        name = " ".join(self.cleaned_data["name"].split())
        normalized = name.casefold()
        matches = Journal.objects.filter(normalized_name=normalized)
        if self.instance.pk:
            matches = matches.exclude(pk=self.instance.pk)
        if matches.exists():
            raise forms.ValidationError("目录中已有相同名称的期刊，请选择现有条目或调整名称。")
        return name


class ManagedUserCreateForm(forms.ModelForm):
    archive_groups = forms.ModelMultipleChoiceField(label="项目/课题组", queryset=ArchiveGroup.objects.none(), required=False, widget=forms.CheckboxSelectMultiple)

    class Meta:
        model = get_user_model()
        fields = ("username",)
        field_classes = {"username": UsernameField}
        labels = {"username": "用户名（必填）"}

    def clean_username(self):
        username = self.cleaned_data["username"]
        if get_user_model().objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("该用户名已存在，请使用其他用户名。")
        return username

    def __init__(self, *args, group_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["archive_groups"].queryset = group_queryset or ArchiveGroup.objects.none()


class ManagedUserEditForm(forms.ModelForm):
    archive_groups = forms.ModelMultipleChoiceField(label="项目/课题组", queryset=ArchiveGroup.objects.none(), required=False, widget=forms.CheckboxSelectMultiple)

    class Meta:
        model = get_user_model()
        fields = ("username", "first_name", "last_name", "email", "is_active", "is_staff")
        labels = {"username": "用户名", "first_name": "名", "last_name": "姓", "email": "邮箱", "is_active": "账户启用", "is_staff": "管理员权限"}

    def __init__(self, *args, group_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        groups = group_queryset or ArchiveGroup.objects.none()
        self.fields["archive_groups"].queryset = groups
        self.fields["archive_groups"].initial = self.instance.archive_groups.all()

    def save(self, commit=True):
        user = super().save(commit=commit)
        if commit:
            user.archive_groups.set(self.cleaned_data["archive_groups"])
        return user


class PersonalProfileForm(forms.ModelForm):
    class Meta:
        model = get_user_model()
        fields = ("first_name", "last_name", "email")
        labels = {"first_name": "名", "last_name": "姓", "email": "邮箱"}


class ArchiveGroupForm(forms.ModelForm):
    class Meta:
        model = ArchiveGroup
        fields = ["name", "description", "members", "managers", "is_active"]
        labels = {"name": "分组名称", "description": "分组说明", "members": "成员", "managers": "项目负责人/集合管理员", "is_active": "启用"}
        widgets = {"members": forms.CheckboxSelectMultiple, "managers": forms.CheckboxSelectMultiple}

    def clean(self):
        cleaned = super().clean()
        members = cleaned.get("members")
        managers = cleaned.get("managers")
        if members is not None and managers is not None and managers.exclude(pk__in=members.values("pk")).exists():
            self.add_error("managers", "项目负责人或集合管理员必须是该组成员。")
        return cleaned
