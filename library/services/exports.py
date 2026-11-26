"""Permission-filtered publication exports; generated entirely from current metadata."""
import csv
import io
import re
import zipfile
from xml.etree.ElementTree import Element, SubElement, tostring

from django.http import HttpResponse


HEADERS = ["标题", "作者", "发表年份", "发表月份", "发表日期", "期刊/来源", "卷", "期", "页码", "DOI", "链接", "项目组"]


def publication_row(material):
    publication = getattr(material, "publication", None)
    if not publication:
        return [material.title] + [""] * 10 + ["、".join(group.name for group in material.groups.all())]
    return [material.title, publication.authors, publication.publication_year or "", publication.publication_month or "", publication.publication_date.isoformat() if publication.publication_date else "", publication.displayed_journal, publication.volume, publication.issue, publication.pages, publication.doi, publication.url, "、".join(group.name for group in material.groups.all())]


def bibliography(material):
    row = publication_row(material)
    values = dict(zip(["title", "author", "year", "month", "date", "journal", "volume", "number", "pages", "doi", "url"], row[:11]))
    def escape(value):
        return re.sub(r"[\\{}]", lambda match: {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}"}[match.group()], str(value)).replace("\r", " ").replace("\n", " ")
    fields = [f"  {key} = {{{escape(value)}}}" for key, value in values.items() if value != ""]
    return f"@article{{material{material.pk},\n" + ",\n".join(fields) + "\n}\n"


def spreadsheet(rows):
    # Inline strings preserve DOI/page formatting and never become Excel formulas.
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    sheet = Element("worksheet", xmlns=ns)
    data = SubElement(sheet, "sheetData")
    for row in rows:
        output_row = SubElement(data, "row")
        for value in row:
            cell = SubElement(output_row, "c", t="inlineStr")
            text = SubElement(SubElement(cell, "is"), "t", {"{http://www.w3.org/XML/1998/namespace}space": "preserve"})
            text.text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value))
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        archive.writestr("_rels/.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        archive.writestr("xl/workbook.xml", f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="成果清单" sheetId="1" r:id="rId1"/></sheets></workbook>')
        archive.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        archive.writestr("xl/worksheets/sheet1.xml", tostring(sheet, encoding="utf-8", xml_declaration=True))
    return output.getvalue()


def export_response(items, format):
    if format == "bib":
        content = "\n".join(bibliography(item) for item in items)
        content_type = "application/x-bibtex; charset=utf-8"
    elif format == "xlsx":
        content = spreadsheet([HEADERS] + [publication_row(item) for item in items])
        content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(HEADERS)
        for item in items:
            writer.writerow(["'" + str(value) if str(value).lstrip().startswith(("=", "+", "-", "@")) or str(value).startswith(("\t", "\r", "\n")) else value for value in publication_row(item)])
        content = "\ufeff" + output.getvalue()
        content_type = "text/csv; charset=utf-8"
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="publications.{format}"'
    response["Cache-Control"] = "no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
