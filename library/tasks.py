import hashlib
import logging
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from PIL import Image
from docx import Document as DocxDocument
from django.core.files import File
from django.db import transaction
from pypdf import PdfReader

from .models import MaterialVersion
from .search import index_version

logger = logging.getLogger(__name__)


def _is_docx(path):
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 5000 or sum(item.file_size for item in entries) > 250_000_000:
                return False
            return any(item.filename == "word/document.xml" for item in entries)
    except (OSError, zipfile.BadZipFile):
        return False


def process_version(version_id):
    version = MaterialVersion.objects.select_related("material").get(pk=version_id)
    version.status = MaterialVersion.Status.PROCESSING
    version.error_message = ""
    version.content_type = "application/octet-stream"
    version.save(update_fields=["status", "error_message"])
    try:
        path = Path(version.original_file.path)
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        version.sha256 = digest.hexdigest()
        with path.open("rb") as source:
            signature = source.read(8)
        if version.extension == ".pdf" and signature.startswith(b"%PDF-"):
            reader = PdfReader(str(path), strict=False)
            version.extracted_text = "\n".join(page.extract_text() or "" for page in reader.pages)[:1_000_000]
            version.content_type = "application/pdf"
        elif version.extension == ".docx" and _is_docx(path):
            document = DocxDocument(str(path))
            paragraphs = [paragraph.text for paragraph in document.paragraphs]
            table_cells = [cell.text for table in document.tables for row in table.rows for cell in row.cells]
            version.extracted_text = "\n".join(paragraphs + table_cells)[:1_000_000]
            _create_docx_preview(version, path)
            version.content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif version.extension == ".png" and signature == b"\x89PNG\r\n\x1a\n":
            with Image.open(path) as image:
                image.verify()
            version.content_type = "image/png"
        else:
            if version.extension in {".pdf", ".docx", ".png"}:
                raise ValueError("File signature does not match its extension")
        version.status = MaterialVersion.Status.READY
    except Exception as exc:
        logger.exception("Could not process material version %s", version_id)
        version.status = MaterialVersion.Status.FAILED
        version.error_message = f"文件解析失败：{type(exc).__name__}"[:500]
    version.save(update_fields=["sha256", "extracted_text", "content_type", "status", "error_message", "preview_file"])
    index_version(version)
    return version.status


def _create_docx_preview(version, source_path):
    with tempfile.TemporaryDirectory(prefix="teaminfo-docx-") as temporary_directory:
        temp = Path(temporary_directory)
        safe_input = temp / "source.docx"
        shutil.copyfile(source_path, safe_input)
        profile = temp / "profile"
        subprocess.run(
            ["libreoffice", f"-env:UserInstallation={profile.as_uri()}", "--headless", "--convert-to", "pdf", "--outdir", str(temp), str(safe_input)],
            check=True,
            capture_output=True,
            timeout=120,
        )
        output = temp / "source.pdf"
        if not output.exists():
            raise RuntimeError("LibreOffice did not create a preview file")
        with output.open("rb") as pdf:
            version.preview_file.save(f"{version.pk}.pdf", File(pdf), save=False)

