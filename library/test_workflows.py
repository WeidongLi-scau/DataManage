import csv
import io
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from .models import ArchiveGroup, AuditLog, Material, MaterialCategory, MaterialCollection, MaterialVersion, PublicationMetadata, UploadBatch, UserAccountState
from .services.uploads import create_version


class WorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.admin = users.objects.create_superuser("workflow-admin", password="Starting-password-!123")
        cls.alice = users.objects.create_user("workflow-alice", password="Starting-password-!123")
        cls.bob = users.objects.create_user("workflow-bob", password="Starting-password-!123")
        cls.group = ArchiveGroup.objects.create(name="项目甲")
        cls.group.members.add(cls.alice)
        cls.other = ArchiveGroup.objects.create(name="项目乙")
        cls.other.members.add(cls.bob)
        cls.public = ArchiveGroup.objects.create(name="公共资料", is_public=True)
        cls.category = MaterialCategory.objects.create(name="报告")
        cls.collection = MaterialCollection.objects.create(name="阶段资料", created_by=cls.alice)
        cls.collection.groups.add(cls.group)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        settings = override_settings(MEDIA_ROOT=Path(self.temp.name), MEILI_ENABLED=False)
        settings.enable()
        self.addCleanup(settings.disable)
        self.client.force_login(self.alice)

    def material(self, title="我的资料", owner=None, group=None, content=b"original contents"):
        item = Material.objects.create(title=title, uploaded_by=owner or self.alice)
        item.groups.add(group or self.group)
        version = create_version(material=item, uploaded_file=SimpleUploadedFile("original.txt", content), user=owner or self.alice, version_number=1)
        item.current_version = version
        item.save()
        return item

    def bulk(self, items, **data):
        return self.client.post(reverse("bulk_materials"), {"materials": [item.pk for item in items], **data})

    def test_viewer_does_not_see_edit_replace_delete(self):
        item = self.material(owner=self.bob, group=self.public)
        response = self.client.get(reverse("material_detail", args=[item.pk]))
        self.assertNotContains(response, "编辑资料信息")
        self.assertNotContains(response, "替换文件")
        self.assertNotContains(response, "移入回收站")
        self.assertContains(response, "下载")
        self.assertEqual(self.client.post(reverse("delete_material", args=[item.pk])).status_code, 302)
        item.refresh_from_db()
        self.assertIsNone(item.deleted_at)

    def test_edit_retains_unavailable_groups_collections_and_inactive_groups(self):
        item = self.material()
        inactive = ArchiveGroup.objects.create(name="已停用项目", is_active=False)
        item.groups.add(self.other, inactive)
        hidden = MaterialCollection.objects.create(name="乙组集合", created_by=self.bob)
        hidden.groups.add(self.other)
        item.collections.add(hidden)
        response = self.client.post(reverse("edit_material", args=[item.pk]), {"record_type": "material", "title": "更新说明", "groups": [self.group.pk]})
        self.assertEqual(response.status_code, 302)
        self.assertSetEqual(set(item.groups.all()), {self.group, self.other, inactive})
        self.assertIn(hidden, item.collections.all())

    @override_settings(MEILI_ENABLED=True)
    def test_historical_processing_cannot_overwrite_current_search_document(self):
        from .search import index_version
        item = self.material()
        old = item.current_version
        new = create_version(material=item, uploaded_file=SimpleUploadedFile("current.txt", b"current version text"), user=self.alice, version_number=2)
        item.current_version = new
        item.save()
        with patch("library.search.requests.put") as send, patch("library.search.ensure_index"):
            index_version(old)
            send.assert_not_called()
            index_version(new)
            self.assertEqual(send.call_args.kwargs["json"][0]["original_name"], "current.txt")

    def test_personal_settings_only_updates_profile_fields(self):
        response = self.client.post(reverse("personal_settings"), {"section": "profile", "first_name": "成员", "email": "member@example.test", "is_staff": "on", "username": "changed"})
        self.assertRedirects(response, reverse("personal_settings"))
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.first_name, "成员")
        self.assertFalse(self.alice.is_staff)
        self.assertEqual(self.alice.username, "workflow-alice")

    def test_personal_password_change_keeps_current_session_and_invalidates_others(self):
        other_client = Client()
        other_client.force_login(self.alice)
        response = self.client.post(reverse("personal_settings"), {"section": "password", "old_password": "Starting-password-!123", "new_password1": "Brand-new-secret-!78234", "new_password2": "Brand-new-secret-!78234"})
        self.assertRedirects(response, reverse("personal_settings"))
        self.assertEqual(self.client.get(reverse("home")).status_code, 200)
        self.assertEqual(other_client.get(reverse("home")).status_code, 302)
        self.alice.refresh_from_db()
        self.assertTrue(self.alice.check_password("Brand-new-secret-!78234"))

    def test_password_reset_requires_confirmation_and_forces_change(self):
        self.client.force_login(self.admin)
        url = reverse("reset_member_password", args=[self.alice.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.alice.refresh_from_db()
        self.assertTrue(self.alice.check_password("Starting-password-!123"))
        existing = Client()
        existing.force_login(self.alice)
        response = self.client.post(url)
        password = response.context["credentials"]["password"]
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(existing.get(reverse("home")).status_code, 302)
        self.assertNotContains(self.client.get(url), password)
        self.assertNotIn(password, str(list(AuditLog.objects.values())))
        member = Client()
        response = member.post(reverse("login"), {"username": self.alice.username, "password": password})
        self.assertRedirects(response, reverse("first_password_change"), fetch_redirect_response=False)

    def test_member_cannot_reset_someone_elses_password(self):
        self.assertEqual(self.client.post(reverse("reset_member_password", args=[self.bob.pk])).status_code, 404)

    def test_project_pages_never_include_other_project_records(self):
        mine = self.material()
        self.material("乙组私密文件", owner=self.bob, group=self.other)
        response = self.client.get(reverse("projects"))
        self.assertContains(response, self.group.name)
        self.assertNotContains(response, self.other.name)
        self.assertContains(self.client.get(reverse("project_detail", args=[self.group.pk])), mine.title)
        self.assertEqual(self.client.get(reverse("project_detail", args=[self.other.pk])).status_code, 404)

    def test_my_upload_batches_exclude_other_members(self):
        UploadBatch.objects.create(created_by=self.alice, title="我的上传批次")
        UploadBatch.objects.create(created_by=self.bob, title="他人的私密批次")
        response = self.client.get(reverse("my_upload_batches"))
        self.assertContains(response, "我的上传批次")
        self.assertNotContains(response, "他人的私密批次")

    def test_bulk_category_requires_explicit_confirmation(self):
        item = self.material()
        response = self.bulk([item], action="category", target=self.category.pk)
        self.assertContains(response, "确认执行")
        item.refresh_from_db()
        self.assertIsNone(item.category)
        self.assertEqual(self.bulk([item], action="category", target=self.category.pk, confirmed="yes").status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.category, self.category)

    def test_bulk_mutation_rejects_other_owner_or_invisible_record_atomically(self):
        mine = self.material()
        readable = self.material("公共记录", owner=self.bob, group=self.public)
        self.assertEqual(self.bulk([mine, readable], action="delete", confirmed="yes").status_code, 404)
        private = self.material("私密记录", owner=self.bob, group=self.other)
        self.assertEqual(self.bulk([mine, private], action="delete", confirmed="yes").status_code, 404)
        mine.refresh_from_db()
        self.assertIsNone(mine.deleted_at)

    def test_bulk_download_contains_exact_contents_and_unique_names(self):
        items = [self.material(content=b"first contents"), self.material(content=b"second contents")]
        response = self.bulk(items, action="download")
        self.assertEqual(response.status_code, 200)
        contents = b"".join(response.streaming_content)
        response.close()
        with zipfile.ZipFile(io.BytesIO(contents)) as archive:
            self.assertEqual(archive.read(f"{items[0].pk}/original.txt"), b"first contents")
            self.assertEqual(archive.read(f"{items[1].pk}/original.txt"), b"second contents")

    def test_bulk_collection_validates_project_and_preserves_other_relations(self):
        mine = self.material()
        self.assertEqual(self.bulk([mine], action="add_collection", target=self.collection.pk, confirmed="yes").status_code, 302)
        self.assertIn(self.collection, mine.collections.all())
        self.assertEqual(self.bulk([mine], action="remove_collection", target=self.collection.pk, confirmed="yes").status_code, 302)
        self.assertFalse(mine.collections.exists())
        incompatible = MaterialCollection.objects.create(name="其他项目集合", created_by=self.bob)
        incompatible.groups.add(self.other)
        self.client.force_login(self.admin)
        self.assertContains(self.bulk([mine], action="add_collection", target=incompatible.pk, confirmed="yes"), "不匹配")
        self.assertFalse(mine.collections.exists())

    def test_bulk_group_removal_cannot_leave_no_group_or_break_collection(self):
        item = self.material()
        self.assertContains(self.bulk([item], action="remove_group", target=self.group.pk, confirmed="yes"), "失去全部项目归属")
        self.assertEqual(self.bulk([item], action="add_group", target=self.public.pk, confirmed="yes").status_code, 302)
        item.collections.add(self.collection)
        self.assertContains(self.bulk([item], action="remove_group", target=self.group.pk, confirmed="yes"), "集合与项目不再匹配")
        item.collections.clear()
        self.assertEqual(self.bulk([item], action="remove_group", target=self.group.pk, confirmed="yes").status_code, 302)
        self.assertSetEqual(set(item.groups.all()), {self.public})

    def test_bulk_delete_preserves_shared_file_and_other_record(self):
        first = self.material()
        second = self.material()
        self.assertEqual(first.current_version.stored_file_id, second.current_version.stored_file_id)
        self.bulk([first], action="delete", confirmed="yes")
        second.refresh_from_db()
        self.assertIsNone(second.deleted_at)
        with second.current_version.original_file.open("rb") as source:
            self.assertEqual(source.read(), b"original contents")

    def test_history_download_and_restore_create_new_version(self):
        item = self.material()
        original = item.current_version
        new = create_version(material=item, uploaded_file=SimpleUploadedFile("new.txt", b"new version contents"), user=self.alice, version_number=2)
        item.current_version = new
        item.save()
        response = self.client.get(reverse("version_download", args=[item.pk, original.pk]))
        self.assertEqual(b"".join(response.streaming_content), b"original contents")
        response.close()
        with self.captureOnCommitCallbacks(execute=True), patch("library.views._enqueue_processing"):
            response = self.client.post(reverse("restore_version", args=[item.pk, original.pk]))
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.versions.count(), 3)
        self.assertEqual(item.current_version.version_number, 3)
        self.assertEqual(item.current_version.original_file.name, original.original_file.name)

    def test_history_endpoints_reject_cross_record_or_project_version_ids(self):
        mine = self.material()
        hidden = self.material(owner=self.bob, group=self.other)
        self.assertEqual(self.client.get(reverse("version_download", args=[mine.pk, hidden.current_version_id])).status_code, 404)
        self.assertEqual(self.client.post(reverse("restore_version", args=[hidden.pk, hidden.current_version_id])).status_code, 404)

    def test_exports_obey_filters_permissions_and_preserve_structured_fields(self):
        visible = self.material("论文标题")
        visible.record_type = "publication"
        visible.save()
        PublicationMetadata.objects.create(material=visible, authors="A and B", publication_year=2023, publication_month=9, journal_name="Example Journal", volume="10", issue="17", pages="15679-15689", doi="10.1234/test")
        hidden = self.material("私密论文", owner=self.bob, group=self.other)
        hidden.record_type = "publication"
        hidden.save()
        PublicationMetadata.objects.create(material=hidden, publication_year=2023)
        url = reverse("publications")
        csv_result = self.client.get(url, {"year": "2023", "format": "csv"})
        rows = list(csv.reader(io.StringIO(csv_result.content.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][0], "论文标题")
        self.assertEqual(rows[1][8], "15679-15689")
        empty = self.client.get(url, {"year": "2024", "format": "csv"})
        self.assertNotContains(empty, "论文标题")
        self.assertEqual(self.client.get(url, {"group": self.other.pk, "format": "csv"}).status_code, 404)
        bib = self.client.get(url, {"format": "bib"})
        import bibtexparser
        entries = bibtexparser.parse_string(bib.content.decode()).entries
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].fields_dict["doi"].value, "10.1234/test")
        xlsx = self.client.get(url, {"format": "xlsx"})
        with zipfile.ZipFile(io.BytesIO(xlsx.content)) as archive:
            sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
            text = " ".join(sheet.itertext())
            self.assertIn("论文标题", text)
            self.assertNotIn("私密论文", text)
            self.assertFalse(any(element.tag.endswith("}f") for element in sheet.iter()))

    def test_csv_formula_like_text_is_exported_as_plain_text(self):
        item = self.material('=HYPERLINK("https://example.test")')
        item.record_type = "publication"
        item.save()
        PublicationMetadata.objects.create(material=item)
        response = self.client.get(reverse("publications"), {"format": "csv"})
        row = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))[1]
        self.assertTrue(row[0].startswith("'="))
