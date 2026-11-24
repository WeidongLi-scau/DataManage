from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import ArchiveGroup, Material, MaterialCategory
from .permissions import accessible_materials


class MaterialAccessTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.alice = user_model.objects.create_user(username="alice", password="test-password-!2026")
        self.bob = user_model.objects.create_user(username="bob", password="test-password-!2026")
        self.public = ArchiveGroup.objects.create(name="公共资料", is_public=True)
        self.private = ArchiveGroup.objects.create(name="项目甲")
        self.private.members.add(self.alice)
        self.category = MaterialCategory.objects.create(name="报告")
        self.visible = Material.objects.create(title="公开资料", uploaded_by=self.alice)
        self.visible.groups.add(self.public)
        self.restricted = Material.objects.create(title="项目甲资料", uploaded_by=self.alice)
        self.restricted.groups.add(self.private)

    def test_public_group_is_visible_to_every_active_user(self):
        self.assertQuerySetEqual(
            accessible_materials(self.bob).order_by("title"),
            [self.visible],
            transform=lambda item: item,
        )

    def test_private_group_is_hidden_from_non_members(self):
        self.assertQuerySetEqual(
            accessible_materials(self.bob).order_by("title"),
            [self.visible],
            transform=lambda item: item,
        )

    def test_private_group_is_available_to_members(self):
        self.assertCountEqual(accessible_materials(self.alice), [self.visible, self.restricted])

    def test_unauthorized_detail_returns_not_found(self):
        self.client.force_login(self.bob)
        response = self.client.get(reverse("material_detail", args=[self.restricted.pk]))
        self.assertEqual(response.status_code, 404)

    def test_home_and_upload_pages_render_for_member(self):
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(reverse("home")).status_code, 200)
        self.assertEqual(self.client.get(reverse("upload")).status_code, 200)

    def test_first_login_requires_password_change(self):
        response = self.client.post(
            reverse("login"),
            {"username": "alice", "password": "test-password-!2026"},
        )
        self.assertRedirects(response, reverse("first_password_change"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("home")).status_code, 302)
        self.assertEqual(self.client.get(reverse("first_password_change")).status_code, 200)

    @override_settings(MAX_UPLOAD_SIZE=5)
    def test_upload_rejects_file_over_limit(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from unittest.mock import patch

        self.client.force_login(self.alice)
        with patch("library.views._enqueue_processing"):
            response = self.client.post(
                reverse("upload"),
                {
                    "title": "",
                    "description": "",
                    "category": "",
                    "tags": "",
                    "groups": [self.private.pk],
                    "files": SimpleUploadedFile("sample.bin", b"0123456789"),
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Material.objects.filter(title="sample").exists())

    def test_valid_file_upload_creates_material(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.client.force_login(self.alice)
        with tempfile.TemporaryDirectory() as directory:
            with override_settings(MEDIA_ROOT=Path(directory), MAX_UPLOAD_SIZE=100):
                with patch("library.views._enqueue_processing"):
                    response = self.client.post(
                        reverse("upload"),
                        {
                            "title": "",
                            "description": "说明",
                            "category": "报告",
                            "tags": "季度",
                            "groups": [self.private.pk],
                            "files": SimpleUploadedFile("sample.txt", b"team notes"),
                        },
                    )
        self.assertRedirects(response, reverse("home"))
        material = Material.objects.get(title="sample")
        self.assertEqual(material.groups.get(), self.private)
        self.assertEqual(material.current_version.original_name, "sample.txt")

    def test_pending_image_cannot_be_previewed_before_validation(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.client.force_login(self.alice)
        with tempfile.TemporaryDirectory() as directory:
            with override_settings(MEDIA_ROOT=Path(directory)):
                with patch("library.views._enqueue_processing"):
                    self.client.post(
                        reverse("upload"),
                        {
                            "title": "spoof",
                            "description": "",
                            "category": "",
                            "tags": "",
                            "groups": [self.private.pk],
                            "files": SimpleUploadedFile("spoof.png", b"<script>alert(1)</script>", content_type="image/png"),
                        },
                    )
                material = Material.objects.get(title="spoof")
                response = self.client.get(reverse("preview_material", args=[material.pk]))
        self.assertEqual(response.status_code, 302)

    def test_system_status_is_admin_only(self):
        from pathlib import Path
        import tempfile
        from unittest.mock import patch

        self.client.force_login(self.bob)
        self.assertEqual(self.client.get(reverse("system_status")).status_code, 404)
        admin = get_user_model().objects.create_superuser(username="sysadmin", password="safe-password-!2026")
        self.client.force_login(admin)
        with tempfile.TemporaryDirectory() as directory:
            with override_settings(DATA_ROOT=Path(directory)), patch("requests.get") as health_request:
                health_request.return_value.ok = True
                response = self.client.get(reverse("system_status"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "磁盘可用空间")

    def test_docx_text_is_extracted_by_worker(self):
        import tempfile
        from io import BytesIO
        from pathlib import Path
        from unittest.mock import patch
        from docx import Document as DocxDocument
        from django.core.files.uploadedfile import SimpleUploadedFile
        from library.models import MaterialVersion
        from library.tasks import process_version

        content = BytesIO()
        document = DocxDocument()
        document.add_paragraph("组内检索测试内容")
        document.save(content)
        material = Material.objects.create(title="可搜索文档", uploaded_by=self.alice)
        material.groups.add(self.private)
        with tempfile.TemporaryDirectory() as directory:
            with override_settings(MEDIA_ROOT=Path(directory)):
                version = MaterialVersion.objects.create(
                    material=material,
                    version_number=1,
                    original_file=SimpleUploadedFile("searchable.docx", content.getvalue()),
                    original_name="searchable.docx",
                    extension=".docx",
                    file_size=len(content.getvalue()),
                    created_by=self.alice,
                )
                material.current_version = version
                material.save(update_fields=["current_version"])
                with patch("library.tasks._create_docx_preview"), patch("library.tasks.index_version"):
                    self.assertEqual(process_version(version.pk), MaterialVersion.Status.READY)
                version.refresh_from_db()
        self.assertIn("组内检索测试内容", version.extracted_text)


