import importlib
import io
import json
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import ArchiveGroup, Folder, Material, MaterialCollection
from .services.folders import create_folder, upload_parts
from .services.uploads import create_version


class DirectoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser("folder-admin", password="Test-initial-!3982")
        cls.alice = get_user_model().objects.create_user("folder-alice", password="Test-initial-!3982")
        cls.bob = get_user_model().objects.create_user("folder-bob", password="Test-initial-!3982")
        cls.group = ArchiveGroup.objects.create(name="广东省项目")
        cls.group.members.add(cls.alice)
        cls.other = ArchiveGroup.objects.create(name="其他项目")
        cls.other.members.add(cls.bob)
        cls.root = create_folder(cls.group, None, "项目申请", cls.alice)
        cls.child = create_folder(cls.group, cls.root, "待申请项目", cls.alice)
        cls.year = create_folder(cls.group, cls.child, "2026", cls.alice)
        cls.hidden = create_folder(cls.other, None, "私密文件夹", cls.bob)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        settings = override_settings(MEDIA_ROOT=Path(self.directory.name), MEILI_ENABLED=False)
        settings.enable()
        self.addCleanup(settings.disable)
        self.client.force_login(self.alice)

    def material(self, folder=None, owner=None, title="申请书", content=b"application contents"):
        folder = folder or self.year
        owner = owner or self.alice
        item = Material.objects.create(title=title, uploaded_by=owner, folder=folder)
        item.groups.add(folder.group)
        version = create_version(material=item, uploaded_file=SimpleUploadedFile("application.txt", content), user=owner, version_number=1)
        item.current_version = version
        item.save()
        return item

    def url(self, action, folder=None):
        return reverse(action, args=[self.group.pk, (folder or self.child).pk])

    def test_tree_breadcrumbs_and_unauthorized_directory(self):
        item = self.material()
        response = self.client.get(self.url("folder_browse", self.year))
        self.assertContains(response, "项目申请")
        self.assertContains(response, "待申请项目")
        self.assertContains(response, item.title)
        self.assertNotContains(response, "私密文件夹")
        self.assertEqual(self.client.get(reverse("folder_browse", args=[self.other.pk, self.hidden.pk])).status_code, 404)

    def test_project_manager_can_browse_and_create_without_duplicate_membership(self):
        self.other.managers.add(self.alice)
        self.assertEqual(self.client.get(reverse("folder_root", args=[self.other.pk])).status_code, 200)
        response = self.client.post(reverse("root_folder_create", args=[self.other.pk]), {"name": "负责人创建"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Folder.objects.filter(group=self.other, name="负责人创建").exists())

    def test_create_duplicate_rename_move_cycle_and_same_name_conflict(self):
        self.assertContains(self.client.post(self.url("folder_create", self.root), {"name": self.child.name}), "同级目录已有")
        response = self.client.post(self.url("folder_create", self.year), {"name": "具体项目", "description": "申请材料"})
        self.assertEqual(response.status_code, 302)
        created = Folder.objects.get(name="具体项目")
        self.assertEqual(created.parent, self.year)
        self.assertContains(self.client.post(self.url("folder_move", self.child), {"parent": created.pk}), "不能移动到自身")
        self.assertEqual(self.client.post(self.url("folder_rename", created), {"name": "新的项目名称"}).status_code, 302)
        created.refresh_from_db()
        self.assertEqual(created.name, "新的项目名称")
        self.assertEqual(self.client.post(self.url("folder_move", created), {"parent": self.root.pk}).status_code, 302)
        created.refresh_from_db()
        self.assertEqual(created.parent, self.root)

    def test_path_traversal_names_and_relative_upload_paths_rejected(self):
        for path in ("../secret.txt", "/absolute/x.txt", "root/../x.txt", "root//x.txt", "root\\x.txt"):
            with self.subTest(path=path), self.assertRaises(ValidationError):
                upload_parts(path)
        for name in ("..", "bad/name", "bad\\name", "\x00"):
            self.assertEqual(self.client.post(self.url("folder_create"), {"name": name}).status_code, 200)

    def test_directory_upload_preserves_nested_paths_and_duplicate_files(self):
        with patch("library.views._enqueue_processing"):
            response = self.client.post(reverse("upload"), {
                "folder": self.year.pk, "record_type": "material", "groups": [self.group.pk],
                "relative_paths": json.dumps(["具体项目/附件/a.txt", "具体项目/附件/a.txt"]),
                "files": [SimpleUploadedFile("a.txt", b"one"), SimpleUploadedFile("a.txt", b"two")],
            })
        self.assertEqual(response.status_code, 302)
        folder = Folder.objects.get(name="附件")
        self.assertEqual(folder.parent.name, "具体项目")
        self.assertEqual(folder.parent.parent, self.year)
        self.assertEqual(Material.objects.filter(folder=folder).count(), 2)

    def test_upload_rejects_forged_path_and_mismatched_project(self):
        for paths, groups in ((["../a.txt"], [self.group.pk]), (["a.txt"], [self.other.pk]), (["wrong.txt"], [self.group.pk])):
            response = self.client.post(reverse("upload"), {"folder": self.year.pk, "record_type": "material", "groups": groups, "relative_paths": json.dumps(paths), "files": SimpleUploadedFile("a.txt", b"test")})
            self.assertEqual(response.status_code, 200)
        self.assertFalse(Material.objects.exists())

    def test_search_scopes_and_folder_name_search(self):
        direct = self.material(folder=self.child, title="共同关键词直接文件")
        deep = self.material(title="共同关键词下级文件")
        elsewhere = self.material(folder=self.root, title="共同关键词其他位置")
        self.material(folder=self.hidden, owner=self.bob, title="共同关键词私密文件")
        url = self.url("folder_browse")
        response = self.client.get(url, {"q": "共同关键词", "scope": "current"})
        self.assertContains(response, direct.title)
        self.assertNotContains(response, deep.title)
        response = self.client.get(url, {"q": "共同关键词", "scope": "subtree"})
        self.assertContains(response, deep.title)
        self.assertNotContains(response, elsewhere.title)
        response = self.client.get(url, {"q": "共同关键词", "scope": "all"})
        self.assertContains(response, elsewhere.title)
        self.assertNotContains(response, "共同关键词私密文件")
        response = self.client.get(url, {"q": "2026", "scope": "subtree"})
        self.assertTrue(response.context["folders"].filter(pk=self.year.pk).exists())

    def test_home_folder_filter_includes_descendants_and_keeps_legacy_collections(self):
        direct = self.material(folder=self.child, title="上层文件")
        deep = self.material(title="子目录新上传文件")
        outside = self.material(folder=self.root, title="目录外文件")
        legacy = MaterialCollection.objects.create(name="旧论文集合", created_by=self.alice)
        legacy.groups.add(self.group)
        direct.collections.add(legacy)
        response = self.client.get(reverse("home"), {"folder": self.child.pk})
        ids = {item.pk for item in response.context["page_obj"]}
        self.assertEqual(ids, {direct.pk, deep.pk})
        self.assertContains(response, "归档文件夹")
        self.assertContains(response, "旧资料集合（兼容筛选）")
        self.assertContains(response, "打开此文件夹")
        with patch("library.views.search_materials", return_value=[direct, deep, outside]):
            response = self.client.get(reverse("home"), {"q": "文件", "folder": self.child.pk})
        self.assertEqual({item.pk for item in response.context["page_obj"]}, ids)
        response = self.client.get(reverse("home"), {"collection": legacy.pk})
        self.assertEqual([item.pk for item in response.context["page_obj"]], [direct.pk])

    def test_home_folder_filter_never_exposes_other_projects(self):
        self.material()
        self.material(folder=self.hidden, owner=self.bob, title="隐藏文件")
        for value in (str(self.hidden.pk), "invalid", "999999"):
            response = self.client.get(reverse("home"), {"folder": value})
            self.assertEqual(response.context["page_obj"].paginator.count, 0)
            self.assertContains(response, "请重新选择")
            self.assertContains(response, "没有符合条件的资料")
            self.assertNotContains(response, "私密文件夹")
        self.group.members.add(self.bob)
        self.client.force_login(self.bob)
        response = self.client.get(reverse("home"), {"folder": self.year.pk, "group": self.other.pk})
        self.assertEqual(response.context["page_obj"].paginator.count, 0)

    def test_tree_expands_current_path_and_marks_current_folder(self):
        response = self.client.get(self.url("folder_browse", self.year))
        node = next(node for node in response.context["tree_nodes"] if node["folder"].pk == self.root.pk)
        self.assertTrue(node["expanded"])
        self.assertTrue(node["children"][0]["expanded"])
        self.assertContains(response, 'class="tree-current-label">当前')

    def test_move_records_preserves_versions_and_cross_project_requires_admin_confirmation(self):
        item = self.material()
        original = item.current_version_id
        data = {"materials": [item.pk], "folder": self.root.pk, "confirmed": "yes"}
        self.assertEqual(self.client.post(reverse("move_materials"), data).status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.folder, self.root)
        self.assertEqual(item.current_version_id, original)
        self.client.force_login(self.admin)
        data["folder"] = self.hidden.pk
        self.assertContains(self.client.post(reverse("move_materials"), data), "请明确确认")
        item.refresh_from_db()
        self.assertEqual(item.folder, self.root)
        data["authorization_confirmed"] = "yes"
        self.assertEqual(self.client.post(reverse("move_materials"), data).status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.folder, self.hidden)
        self.assertEqual(list(item.groups.all()), [self.other])
        self.assertEqual(item.current_version_id, original)

    def test_download_preserves_tree_and_duplicate_filenames(self):
        a = self.material(content=b"first")
        b = self.material(content=b"second")
        response = self.client.get(reverse("folder_download", args=[self.child.pk]))
        payload = b"".join(response.streaming_content)
        response.close()
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertEqual(archive.read("待申请项目/2026/application.txt"), b"second")
            self.assertEqual(archive.read(f"待申请项目/2026/{a.pk}-application.txt"), b"first")
        self.assertEqual(self.client.get(reverse("folder_download", args=[self.hidden.pk])).status_code, 404)

    def test_delete_restore_preserves_prior_deleted_files_and_handles_name_conflict(self):
        active = self.material()
        prior = self.material(title="此前已删除文件")
        from django.utils import timezone
        prior.deleted_at = timezone.now()
        prior.save()
        self.assertEqual(self.client.post(self.url("folder_delete")).status_code, 302)
        active.refresh_from_db()
        self.assertIsNotNone(active.deleted_at)
        self.assertEqual(self.client.get(reverse("material_detail", args=[active.pk])).status_code, 404)
        self.assertFalse(Path(self.directory.name).joinpath(active.current_version.original_file.name).is_dir())
        create_folder(self.group, self.root, self.child.name, self.alice)
        self.client.force_login(self.admin)
        restore = reverse("folder_restore", args=[self.child.pk])
        self.assertContains(self.client.post(restore, {"name": self.child.name, "parent": self.root.pk}), "已有同名目录")
        self.assertEqual(self.client.post(restore, {"name": "恢复的目录", "parent": self.root.pk}).status_code, 302)
        active.refresh_from_db(); prior.refresh_from_db(); self.year.refresh_from_db()
        self.assertIsNone(active.deleted_at)
        self.assertIsNotNone(prior.deleted_at)
        self.assertIsNone(self.year.deleted_at)
        self.assertTrue(active.current_version.original_file.storage.exists(active.current_version.original_file.name))

    def test_folder_creator_cannot_delete_other_members_records(self):
        self.material(owner=self.bob)
        self.assertEqual(self.client.post(self.url("folder_delete")).status_code, 302)
        self.child.refresh_from_db()
        self.assertIsNone(self.child.deleted_at)

    def test_batch_folder_move_download_delete_and_reject_unauthorized_selection(self):
        one = create_folder(self.group, self.root, "文件夹甲", self.alice)
        two = create_folder(self.group, self.root, "文件夹乙", self.alice)
        self.material(folder=one, content=b"first")
        self.material(folder=two, content=b"second")
        url = reverse("batch_folders")
        response = self.client.post(url, {"folders": [one.pk, two.pk], "operation": "download"})
        payload = b"".join(response.streaming_content); response.close()
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertEqual(len([name for name in archive.namelist() if name.endswith("application.txt")]), 2)
        response = self.client.post(url, {"folders": [one.pk, two.pk], "operation": "move", "parent": self.year.pk, "confirmed": "yes"})
        self.assertEqual(response.status_code, 302)
        one.refresh_from_db(); two.refresh_from_db()
        self.assertEqual(one.parent, self.year)
        self.assertEqual(two.parent, self.year)
        self.assertEqual(self.client.post(url, {"folders": [one.pk, self.hidden.pk], "operation": "delete", "confirmed": "yes"}).status_code, 404)
        self.assertEqual(self.client.post(url, {"folders": [one.pk, two.pk], "operation": "delete", "confirmed": "yes"}).status_code, 302)
        one.refresh_from_db(); two.refresh_from_db()
        self.assertIsNotNone(one.deleted_at)
        self.assertIsNotNone(two.deleted_at)

    def test_cross_project_folder_move_requires_confirmation_and_updates_entire_tree(self):
        item = self.material()
        self.client.force_login(self.admin)
        url = self.url("folder_move")
        self.assertContains(self.client.post(url, {"parent": self.hidden.pk}), "明确确认授权变化")
        response = self.client.post(url, {"parent": self.hidden.pk, "authorization_confirmed": "yes"})
        self.assertEqual(response.status_code, 302)
        self.child.refresh_from_db(); self.year.refresh_from_db(); item.refresh_from_db()
        self.assertEqual(self.child.group, self.other)
        self.assertEqual(self.year.group, self.other)
        self.assertEqual(list(item.groups.all()), [self.other])
        self.assertEqual(item.folder, self.year)

    def test_migration_retains_multigroup_authorization_and_maps_old_collection(self):
        old = Material.objects.create(title="旧跨组资料", uploaded_by=self.alice)
        old.groups.add(self.group, self.other)
        single = Material.objects.create(title="旧集合资料", uploaded_by=self.alice)
        single.groups.add(self.group)
        collection = MaterialCollection.objects.create(name="旧资料集合", created_by=self.alice)
        collection.groups.add(self.group)
        single.collections.add(collection)
        migration = importlib.import_module("library.migrations.0008_archive_existing_materials")
        migration.archive_existing(apps, SimpleNamespace(connection=SimpleNamespace(alias="default")))
        old.refresh_from_db(); single.refresh_from_db()
        self.assertEqual(old.folder.name, "待整理资料")
        self.assertEqual(set(old.groups.all()), {self.group, self.other})
        self.assertEqual(single.folder.name, collection.name)
        self.assertEqual(single.collections.get(), collection)
        self.client.force_login(self.bob)
        response = self.client.get(reverse("folder_root", args=[self.other.pk]))
        self.assertContains(response, old.title)
        response = self.client.get(reverse("material_detail", args=[old.pk]))
        self.assertNotContains(response, "归档位置")
