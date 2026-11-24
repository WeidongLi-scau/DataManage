from django import forms
from django.contrib.auth import authenticate

from .models import ArchiveGroup, MaterialCategory


def category_choices(include_inactive=False, current=""):
    categories = MaterialCategory.objects.all()
    if not include_inactive:
        categories = categories.filter(is_active=True)
    names = list(categories.values_list("name", flat=True))
    if current and current not in names:
        names.append(current)
    return [("", "未分类")] + [(name, name) for name in names]


class LoginForm(forms.Form):
    username = forms.CharField(label="用户名", max_length=150)
    password = forms.CharField(label="密码", strip=False, widget=forms.PasswordInput)


class UploadMetadataForm(forms.Form):
    title = forms.CharField(label="标题（可留空，默认使用文件名）", max_length=255, required=False)
    description = forms.CharField(label="资料说明", required=False, widget=forms.Textarea(attrs={"rows": 3}))
    category = forms.ChoiceField(label="分类", choices=[("", "未分类")], required=False)
    tags = forms.CharField(label="标签（逗号分隔）", max_length=500, required=False)
    groups = forms.ModelMultipleChoiceField(
        label="可见分组",
        queryset=ArchiveGroup.objects.none(),
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, group_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["groups"].queryset = group_queryset or ArchiveGroup.objects.none()
        self.fields["category"].choices = category_choices()


class MaterialMetadataForm(forms.Form):
    title = forms.CharField(label="标题", max_length=255)
    description = forms.CharField(label="资料说明", required=False, widget=forms.Textarea(attrs={"rows": 4}))
    category = forms.ChoiceField(label="分类", choices=[("", "未分类")], required=False)
    tags = forms.CharField(label="标签（逗号分隔）", max_length=500, required=False)
    groups = forms.ModelMultipleChoiceField(
        label="可见分组",
        queryset=ArchiveGroup.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )

    def __init__(self, *args, group_queryset=None, include_groups=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["groups"].queryset = group_queryset or ArchiveGroup.objects.none()
        self.fields["category"].choices = category_choices(include_inactive=True, current=self.initial.get("category", ""))
        if not include_groups:
            self.fields.pop("groups")

