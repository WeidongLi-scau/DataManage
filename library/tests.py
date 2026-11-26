from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import ArchiveGroup, AuditLog, Journal, Material, MaterialCategory, MaterialCollection, PublicationMetadata, UploadBatch, StoredFile, UserAccountState
from .permissions import accessible_materials, accessible_collections


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

    def test_upload_defaults_to_only_private_group_without_public_sharing(self):
        self.client.force_login(self.alice)
        response = self.client.get(reverse("upload"))
        self.assertEqual(response.context["form"].initial["groups"], [self.private.pk])
        self.assertContains(response, "必选 · 至少一项")
        self.client.force_login(self.bob)
        self.assertNotIn("groups", self.client.get(reverse("upload")).context["form"].initial)

    def test_upload_multiple_private_groups_require_explicit_selection(self):
        another = ArchiveGroup.objects.create(name="另一个项目")
        another.members.add(self.alice)
        self.client.force_login(self.alice)
        response = self.client.get(reverse("upload"))
        self.assertNotIn("groups", response.context["form"].initial)

    def test_upload_missing_or_unauthorized_groups_are_rejected(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.client.force_login(self.bob)
        for groups in ([], [self.private.pk]):
            with self.subTest(groups=groups):
                response = self.client.post(reverse("upload"), {
                    "record_type": "material", "groups": groups,
                    "files": SimpleUploadedFile("blocked.txt", b"do not store"),
                })
                self.assertEqual(response.status_code, 200)
                self.assertIn("groups", response.context["form"].errors)
        self.assertFalse(Material.objects.filter(title="blocked").exists())

    def test_empty_upload_post_shows_error_instead_of_server_error(self):
        self.client.force_login(self.alice)
        response = self.client.post(reverse("upload"), {})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "请选择至少一个文件。")

    def test_upload_file_metadata_order_and_shared_fallback(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.client.force_login(self.alice)
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=Path(directory)), patch("library.views._enqueue_processing"):
            response = self.client.post(reverse("upload"), {
                "record_type": "material", "groups": [self.private.pk],
                "description": "共同说明", "tags": "共同标签",
                "file_0_title": "第二份文件的新标题", "file_0_description": "", "file_0_tags": "",
                "file_1_title": "追加文件", "file_1_description": "单独说明", "file_1_tags": "单独标签",
                "files": [SimpleUploadedFile("second.txt", b"second"), SimpleUploadedFile("added.txt", b"added")],
            })
        self.assertEqual(response.status_code, 302)
        first = Material.objects.get(title="第二份文件的新标题")
        second = Material.objects.get(title="追加文件")
        self.assertEqual(first.current_version.original_name, "second.txt")
        self.assertEqual(first.description, "共同说明")
        self.assertEqual(first.tags, "共同标签")
        self.assertEqual(second.current_version.original_name, "added.txt")
        self.assertEqual(second.description, "单独说明")

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
                    "record_type": "material",
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
                            "category": self.category.pk,
                            "tags": "季度",
                            "record_type": "material",
                            "groups": [self.private.pk],
                            "files": SimpleUploadedFile("sample.txt", b"team notes"),
                        },
                    )
        batch = UploadBatch.objects.get(created_by=self.alice)
        self.assertRedirects(response, reverse("upload_batch_detail", args=[batch.pk]))
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
                            "record_type": "material",
                            "groups": [self.private.pk],
                            "files": SimpleUploadedFile("spoof.png", b"<script>alert(1)</script>", content_type="image/png"),
                        },
                    )
                material = Material.objects.get(title="spoof")
                response = self.client.get(reverse("preview_material", args=[material.pk]))
        self.assertEqual(response.status_code, 302)

    def test_pdf_preview_is_embedded_and_independent_of_text_extraction_status(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from django.core.files.uploadedfile import SimpleUploadedFile
        from library.models import MaterialVersion
        from library.tasks import process_version

        material = Material.objects.create(title="PDF 在线预览", uploaded_by=self.alice)
        material.groups.add(self.private)
        pdf_bytes = b"%PDF-1.4\nnot a parseable PDF body"
        self.client.force_login(self.alice)
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=Path(directory)):
            version = MaterialVersion.objects.create(
                material=material,
                version_number=1,
                original_file=SimpleUploadedFile("preview.pdf", pdf_bytes, content_type="application/pdf"),
                original_name="preview.pdf",
                extension=".pdf",
                file_size=len(pdf_bytes),
                created_by=self.alice,
            )
            material.current_version = version
            material.save(update_fields=["current_version"])
            with patch("library.tasks.index_version"):
                self.assertEqual(process_version(version.pk), MaterialVersion.Status.READY)
            version.refresh_from_db()
            self.assertIn("未加入全文检索", version.error_message)
            page = self.client.get(reverse("preview_material", args=[material.pk]))
            content = self.client.get(reverse("preview_content", args=[material.pk]))

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "<iframe", html=False)
        self.assertContains(page, reverse("preview_content", args=[material.pk]))
        self.assertEqual(content.status_code, 200)
        self.assertEqual(content["Content-Type"], "application/pdf")
        self.assertTrue(content["Content-Disposition"].startswith("inline;"))
        self.assertEqual(content["X-Content-Type-Options"], "nosniff")
        self.assertEqual(content["X-Frame-Options"], "SAMEORIGIN")
        self.assertNotIn("sandbox", content["Content-Security-Policy"])
        self.assertEqual(b"".join(content.streaming_content), pdf_bytes)

    def test_preview_content_endpoint_obeys_group_permissions(self):
        import tempfile
        from pathlib import Path
        from django.core.files.uploadedfile import SimpleUploadedFile
        from library.models import MaterialVersion

        material = Material.objects.create(title="私有 PDF", uploaded_by=self.alice)
        material.groups.add(self.private)
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=Path(directory)):
            version = MaterialVersion.objects.create(
                material=material,
                version_number=1,
                original_file=SimpleUploadedFile("private.pdf", b"%PDF-1.4\nbody"),
                original_name="private.pdf",
                extension=".pdf",
                file_size=13,
                status=MaterialVersion.Status.FAILED,
                created_by=self.alice,
            )
            material.current_version = version
            material.save(update_fields=["current_version"])
            self.client.force_login(self.bob)
            response = self.client.get(reverse("preview_content", args=[material.pk]))
        self.assertEqual(response.status_code, 404)

    def test_sqlite_development_defaults_to_synchronous_processing(self):
        from django.conf import settings

        if settings.DATABASES["default"]["ENGINE"].endswith("sqlite3"):
            self.assertTrue(settings.DEV_SYNC_FILE_PROCESSING)

    def test_pdf_without_extractable_text_remains_previewable(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from django.core.files.uploadedfile import SimpleUploadedFile
        from library.models import MaterialVersion
        from library.tasks import process_version

        material = Material.objects.create(title="内容解析失败但可预览的 PDF", uploaded_by=self.alice)
        material.groups.add(self.private)
        pdf_bytes = b"%PDF-1.4\nnot a parseable PDF body"
        self.client.force_login(self.alice)
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=Path(directory)):
            version = MaterialVersion.objects.create(
                material=material,
                version_number=1,
                original_file=SimpleUploadedFile("extract-failed.pdf", pdf_bytes, content_type="application/pdf"),
                original_name="extract-failed.pdf",
                extension=".pdf",
                file_size=len(pdf_bytes),
                created_by=self.alice,
            )
            material.current_version = version
            material.save(update_fields=["current_version"])
            with patch("library.tasks.index_version"):
                self.assertEqual(process_version(version.pk), MaterialVersion.Status.READY)
            version.refresh_from_db()
            self.assertIn("未加入全文检索", version.error_message)
            preview_page = self.client.get(reverse("preview_material", args=[material.pk]))
            preview_content = self.client.get(reverse("preview_content", args=[material.pk]))
        self.assertEqual(preview_page.status_code, 200)
        self.assertContains(preview_page, "iframe")
        self.assertEqual(preview_content.status_code, 200)
        self.assertEqual(preview_content["Content-Type"], "application/pdf")
        self.assertTrue(preview_content["Content-Disposition"].startswith("inline;"))
        self.assertEqual(preview_content["X-Frame-Options"], "SAMEORIGIN")
        self.assertNotIn("sandbox", preview_content["Content-Security-Policy"])
        self.assertEqual(b"".join(preview_content.streaming_content), pdf_bytes)

    def test_valid_pdf_has_native_inline_preview_content(self):
        import tempfile
        from io import BytesIO
        from pathlib import Path
        from pypdf import PdfWriter
        from django.core.files.uploadedfile import SimpleUploadedFile
        from library.models import MaterialVersion

        output = BytesIO()
        writer = PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.write(output)
        pdf_bytes = output.getvalue()
        material = Material.objects.create(title="有效 PDF", uploaded_by=self.alice)
        material.groups.add(self.private)
        self.client.force_login(self.alice)
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=Path(directory)):
            version = MaterialVersion.objects.create(
                material=material,
                version_number=1,
                original_file=SimpleUploadedFile("valid.pdf", pdf_bytes, content_type="application/pdf"),
                original_name="valid.pdf",
                extension=".pdf",
                file_size=len(pdf_bytes),
                status=MaterialVersion.Status.FAILED,
                created_by=self.alice,
            )
            material.current_version = version
            material.save(update_fields=["current_version"])
            preview_page = self.client.get(reverse("preview_material", args=[material.pk]))
            preview_content = self.client.get(reverse("preview_content", args=[material.pk]))
        self.assertEqual(preview_page.status_code, 200)
        self.assertContains(preview_page, "PDF 预览")
        self.assertContains(preview_page, reverse("preview_content", args=[material.pk]))
        self.assertEqual(preview_content.status_code, 200)
        self.assertEqual(preview_content["Content-Type"], "application/pdf")
        self.assertTrue(preview_content["Content-Disposition"].startswith("inline;"))

    def test_docx_preview_can_use_generated_pdf_even_if_indexing_failed(self):
        import tempfile
        from pathlib import Path
        from django.core.files.uploadedfile import SimpleUploadedFile
        from library.models import MaterialVersion

        material = Material.objects.create(title="DOCX 在线预览", uploaded_by=self.alice)
        material.groups.add(self.private)
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=Path(directory)):
            version = MaterialVersion.objects.create(
                material=material,
                version_number=1,
                original_file=SimpleUploadedFile("report.docx", b"docx original"),
                preview_file=SimpleUploadedFile("report.pdf", b"%PDF-1.4\nconverted"),
                original_name="report.docx",
                extension=".docx",
                file_size=13,
                status=MaterialVersion.Status.FAILED,
                created_by=self.alice,
            )
            material.current_version = version
            material.save(update_fields=["current_version"])
            self.client.force_login(self.alice)
            content = self.client.get(reverse("preview_content", args=[material.pk]))
        self.assertEqual(content.status_code, 200)
        self.assertEqual(content["Content-Type"], "application/pdf")
        self.assertIn("report.pdf", content["Content-Disposition"])

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

    def test_duplicate_upload_reuses_blob_but_keeps_independent_records(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from django.core.files.uploadedfile import SimpleUploadedFile
        from library.models import MaterialVersion

        second_group = ArchiveGroup.objects.create(name="项目乙")
        second_group.members.add(self.alice)
        self.client.force_login(self.alice)
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=Path(directory), MAX_UPLOAD_SIZE=100):
            with patch("library.views._enqueue_processing"):
                response = self.client.post(
                    reverse("upload"),
                    {
                    "record_type": "material",
                    "title": "复用样本",
                    "description": "同一文件的独立记录",
                    "category": self.category.pk,
                    "tags": "测试",
                    "document_date": "2025-03-04",
                    "groups": [self.private.pk, second_group.pk],
                    "files": [
                        SimpleUploadedFile("first.bin", b"same bytes"),
                        SimpleUploadedFile("second.bin", b"same bytes"),
                    ],
                    },
                )
            batch = UploadBatch.objects.get(created_by=self.alice)
            versions = list(MaterialVersion.objects.filter(material__upload_batch=batch).order_by("id"))
            self.assertRedirects(response, reverse("upload_batch_detail", args=[batch.pk]))
            self.assertEqual(len(versions), 2)
            self.assertEqual(StoredFile.objects.filter(sha256=versions[0].sha256).count(), 1)
            self.assertEqual(versions[0].stored_file_id, versions[1].stored_file_id)
            self.assertEqual(versions[0].original_file.name, versions[1].original_file.name)
            self.assertEqual(batch.items.count(), 2)
            self.assertEqual(batch.materials.count(), 2)
            materials = list(batch.materials.order_by("id"))
            self.assertEqual(materials[0].groups.count(), 2)
            self.assertEqual(materials[0].document_date.isoformat(), "2025-03-04")
            with patch("library.views._enqueue_processing"):
                self.client.post(reverse("delete_material", args=[materials[0].pk]))
            download = self.client.get(reverse("download_material", args=[materials[1].pk]))
            self.assertEqual(download.status_code, 200)
            self.assertEqual(b"".join(download.streaming_content), b"same bytes")

    def test_batch_upload_records_one_file_failure_and_continues(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from django.core.files.uploadedfile import SimpleUploadedFile
        from library.models import MaterialVersion
        from library.services.uploads import create_version as save_version

        def save_or_fail(*, material, uploaded_file, user, version_number):
            if uploaded_file.name == "broken.bin":
                raise OSError("simulated storage error")
            return save_version(material=material, uploaded_file=uploaded_file, user=user, version_number=version_number)

        self.client.force_login(self.alice)
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=Path(directory), MAX_UPLOAD_SIZE=100):
            with patch("library.views._enqueue_processing"), patch("library.services.uploads.create_version", side_effect=save_or_fail):
                response = self.client.post(
                    reverse("upload"),
                    {
                        "record_type": "material",
                        "title": "",
                        "description": "",
                        "category": "",
                        "tags": "",
                        "groups": [self.private.pk],
                        "files": [
                            SimpleUploadedFile("broken.bin", b"bad"),
                            SimpleUploadedFile("good.bin", b"good"),
                        ],
                        "file_1_title": "Good file custom title",
                        "file_1_document_date": "2024-02-03",
                        "file_1_category": str(self.category.pk),
                    },
                )
        batch = UploadBatch.objects.get(created_by=self.alice)
        items = list(batch.items.order_by("id"))
        self.assertRedirects(response, reverse("upload_batch_detail", args=[batch.pk]))
        self.assertEqual([item.status for item in items], [MaterialVersion.Status.FAILED, MaterialVersion.Status.PENDING])
        self.assertEqual(batch.materials.count(), 1)
        self.assertEqual(items[0].error_message, "资料记录创建失败；请检查文件后重试。")
        good_material = batch.materials.get()
        self.assertEqual(good_material.title, "Good file custom title")
        self.assertEqual(good_material.document_date.isoformat(), "2024-02-03")
        self.assertEqual(good_material.category, self.category)

    def test_collection_manager_can_create_only_within_managed_group(self):
        self.private.managers.add(self.alice)
        self.client.force_login(self.alice)
        response = self.client.post(
            reverse("manage_collections"),
            {"name": "阶段一", "description": "阶段资料", "groups": [self.private.pk]},
        )
        collection = MaterialCollection.objects.get(name="阶段一")
        self.assertRedirects(response, reverse("manage_collections"))
        self.assertIn(collection, accessible_collections(self.alice))
        self.assertNotIn(collection, accessible_collections(self.bob))
        self.client.force_login(self.bob)
        self.client.post(reverse("manage_collections"), {"name": "越权集合", "groups": [self.private.pk]})
        self.assertFalse(MaterialCollection.objects.filter(name="越权集合").exists())

    def test_bibtex_parser_preserves_structured_fields(self):
        from library.services.bibliography import parse_bibtex

        parsed = parse_bibtex("@article{key, title={Example}, author={Li, Wei and Chen, Ming}, year={2024}, journal={Sample Journal}, volume={8}, number={2}, pages={10--18}, doi={10.1000/example}}")
        self.assertEqual(parsed["title"], "Example")
        self.assertEqual(parsed["publication_year"], 2024)
        self.assertIsNone(parsed["publication_date"])
        self.assertIsNone(parsed["publication_month"])
        self.assertEqual(parsed["issue"], "2")
        self.assertEqual(parsed["pages"], "10--18")

    def test_bibtex_date_parsing_supports_month_names_and_partial_dates(self):
        from datetime import date
        from library.services.bibliography import parse_bibtex

        named_month = parse_bibtex("@article{x, year={2023}, month={sep}}")
        self.assertEqual(named_month["publication_year"], 2023)
        self.assertEqual(named_month["publication_month"], 9)
        self.assertIsNone(named_month["publication_date"])
        complete_date = parse_bibtex("@article{x, date={2023-9-4}, journal={IEEE Internet of Things Journal}}")
        self.assertEqual(complete_date["publication_date"], date(2023, 9, 4))
        self.assertEqual(complete_date["publication_month"], 9)
        self.assertEqual(complete_date["publication_year"], 2023)
        partial_iso_date = parse_bibtex("@article{x, date={2023-09}, journal={Sample Journal}}")
        self.assertEqual(partial_iso_date["publication_year"], 2023)
        self.assertEqual(partial_iso_date["publication_month"], 9)
        self.assertIsNone(partial_iso_date["publication_date"])

    def test_publication_upload_persists_bibtex_and_metadata(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from django.core.files.uploadedfile import SimpleUploadedFile
        journal = Journal.objects.create(name="Test Journal")
        self.client.force_login(self.alice)
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=Path(directory), MAX_UPLOAD_SIZE=100):
            with patch("library.views._enqueue_processing"):
                response = self.client.post(
                    reverse("upload"),
                    {
                        "record_type": "publication",
                        "title": "An article",
                        "description": "",
                        "category": self.category.pk,
                        "tags": "",
                        "groups": [self.private.pk],
                        "collections": [],
                        "bibtex_raw": "@article{x,title={An article},year={2024}}",
                        "authors": "Wei Li",
                        "publication_date": "2024-09-16",
                        "publication_year": "2024",
                        "publication_month": "9",
                        "journal_name": "Test Journal",
                        "volume": "4",
                        "issue": "1",
                        "pages": "1-9",
                        "doi": "10.1000/example",
                        "url": "",
                        "files": SimpleUploadedFile("article.pdf", b"not a pdf"),
                    },
                )
        material = Material.objects.get(title="An article")
        publication = PublicationMetadata.objects.get(material=material)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(publication.publication_year, 2024)
        self.assertEqual(publication.publication_month, 9)
        self.assertEqual(publication.publication_date.isoformat(), "2024-09-16")
        self.assertEqual(publication.journal_id, journal.pk)
        self.assertEqual(publication.displayed_journal, "Test Journal")
        self.assertIn("@article", publication.bibtex_raw)

    def test_bibtex_parse_endpoint_returns_date_and_matches_journal_catalog(self):
        journal = Journal.objects.create(name="IEEE Internet of Things Journal", abbreviation="IEEE IoT J")
        self.client.force_login(self.alice)
        response = self.client.post(
            reverse("parse_bibtex"),
            {"bibtex": "@article{x, title={Example}, author={A Author}, date={2023-9-4}, journal={IEEE Internet of Things Journal}}"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["publication_date"], "2023-09-04")
        self.assertEqual(response.json()["publication_month"], 9)
        self.assertEqual(response.json()["journal_id"], journal.pk)

    def test_bibtex_parse_matches_journal_abbreviation(self):
        journal = Journal.objects.create(name="IEEE Internet of Things Journal", abbreviation="IEEE IoT J")
        self.client.force_login(self.alice)
        response = self.client.post(
            reverse("parse_bibtex"),
            {"bibtex": "@article{x, year={2023}, month={sep}, journal={IEEE IoT J}}"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["journal_id"], journal.pk)
        self.assertEqual(response.json()["journal_name"], journal.name)

    def test_custom_management_home_and_category_form_are_admin_only(self):
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(reverse("management_home")).status_code, 404)
        administrator = get_user_model().objects.create_superuser(username="manager", password="safe-password-!2026")
        self.client.force_login(administrator)
        self.assertEqual(self.client.get(reverse("management_home")).status_code, 200)
        response = self.client.post(reverse("manage_categories"), {"name": "实验记录", "is_active": "on"})
        self.assertRedirects(response, reverse("manage_categories"))
        self.assertTrue(MaterialCategory.objects.filter(name="实验记录", is_active=True).exists())

    def test_management_center_creates_and_edits_member(self):
        administrator = get_user_model().objects.create_superuser(username="account-manager", password="safe-password-!2026")
        self.client.force_login(administrator)
        response = self.client.post(
            reverse("manage_users"),
            {
                "username": "new-member",
                "archive_groups": [self.private.pk],
            },
        )
        member = get_user_model().objects.get(username="new-member")
        self.assertEqual(response.status_code, 200)
        password = response.context["credentials"]["password"]
        self.assertTrue(member.check_password(password))
        self.assertNotEqual(member.password, password)
        self.assertTrue(member.account_state.must_change_password)
        self.assertFalse(member.is_staff)
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertNotContains(self.client.get(reverse("manage_users")), password)
        self.assertNotIn(password, str(dict(self.client.session)))
        self.assertNotIn(password, str(list(AuditLog.objects.values())))
        self.assertIn(self.private, member.archive_groups.all())
        response = self.client.post(
            reverse("edit_managed_user", args=[member.pk]),
            {
                "username": "new-member",
                "first_name": "Updated",
                "last_name": "Member",
                "email": "",
                "is_active": "on",
                "archive_groups": [self.private.pk],
            },
        )
        member.refresh_from_db()
        self.assertRedirects(response, reverse("manage_users"))
        self.assertEqual(member.first_name, "Updated")

    def test_member_creation_form_only_requests_username_and_groups(self):
        administrator = get_user_model().objects.create_superuser(username="simple-manager", password="safe-password-!2026")
        self.client.force_login(administrator)
        response = self.client.get(reverse("manage_users"))
        self.assertEqual(set(response.context["form"].fields), {"username", "archive_groups"})
        response = self.client.post(reverse("manage_users"), {"username": "username-only"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(get_user_model().objects.get(username="username-only").check_password(response.context["credentials"]["password"]))

    def test_generated_passwords_are_distinct_and_duplicate_names_rejected(self):
        administrator = get_user_model().objects.create_superuser(username="unique-manager", password="safe-password-!2026")
        self.client.force_login(administrator)
        passwords = []
        for username in ("generated-one", "generated-two"):
            response = self.client.post(reverse("manage_users"), {"username": username})
            passwords.append(response.context["credentials"]["password"])
        self.assertNotEqual(passwords[0], passwords[1])
        self.assertTrue(all(len(password) >= 24 for password in passwords))
        for username in ("generated-one", "GENERATED-ONE"):
            response = self.client.post(reverse("manage_users"), {"username": username})
            self.assertIn("username", response.context["form"].errors)
            self.assertIsNone(response.context["credentials"])
        self.assertEqual(get_user_model().objects.filter(username__iexact="generated-one").count(), 1)

    def test_regular_member_cannot_create_accounts(self):
        self.client.force_login(self.alice)
        response = self.client.post(reverse("manage_users"), {"username": "not-allowed"})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(get_user_model().objects.filter(username="not-allowed").exists())

    def test_initial_password_requirement_survives_logout_and_session_flag_loss(self):
        self.client.post(reverse("login"), {"username": "alice", "password": "test-password-!2026"})
        self.client.post(reverse("logout"))
        response = self.client.post(reverse("login"), {"username": "alice", "password": "test-password-!2026"})
        self.assertRedirects(response, reverse("first_password_change"), fetch_redirect_response=False)
        session = self.client.session
        session.pop("must_change_password", None)
        session.save()
        self.assertRedirects(self.client.get(reverse("home")), reverse("first_password_change"), fetch_redirect_response=False)
        self.assertRedirects(self.client.get("/admin/"), reverse("first_password_change"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("first_password_change")).status_code, 200)
        response = self.client.post(reverse("first_password_change"), {
            "old_password": "test-password-!2026", "new_password1": "My-personal-password-!8426", "new_password2": "My-personal-password-!8426",
        })
        self.assertRedirects(response, reverse("home"))
        self.assertFalse(UserAccountState.objects.get(user=self.alice).must_change_password)
        self.client.post(reverse("logout"))
        self.assertFalse(self.client.login(username="alice", password="test-password-!2026"))
        response = self.client.post(reverse("login"), {"username": "alice", "password": "My-personal-password-!8426"})
        self.assertRedirects(response, reverse("home"))

    def test_existing_member_is_not_forced_to_change_password_again(self):
        from django.utils import timezone

        self.alice.last_login = timezone.now()
        self.alice.save(update_fields=["last_login"])
        response = self.client.post(reverse("login"), {"username": "alice", "password": "test-password-!2026"})
        self.assertRedirects(response, reverse("home"))
        self.assertFalse(self.alice.account_state.must_change_password)

    def test_multigroup_material_access_uses_any_remaining_group(self):
        second_group = ArchiveGroup.objects.create(name="项目丙")
        second_group.members.add(self.bob)
        material = Material.objects.create(title="跨项目资料", uploaded_by=self.alice)
        material.groups.set([self.private, second_group])
        self.assertIn(material, accessible_materials(self.bob))
        material.groups.remove(second_group)
        self.assertNotIn(material, accessible_materials(self.bob))


