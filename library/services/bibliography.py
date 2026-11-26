import bibtexparser
from datetime import date

from django.utils.dateparse import parse_date


MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


def parse_bibtex(text):
    library = bibtexparser.parse_string(text)
    if not library.entries:
        raise ValueError("BibTeX 中没有可识别的文献条目")
    fields = {name.casefold(): field.value.strip() for name, field in library.entries[0].fields_dict.items()}
    year_text = fields.get("year", "")
    try:
        year = int(year_text[:4]) if year_text else None
    except ValueError:
        year = None
    raw_date = fields.get("date", "").strip()
    normalized_date = raw_date.replace("/", "-")
    date_parts = normalized_date.split("-")
    if date_parts and not year and date_parts[0].isdigit():
        year = int(date_parts[0])
    if len(date_parts) >= 3 and all(part.isdigit() for part in date_parts[:3]):
        normalized_date = "-".join((date_parts[0], date_parts[1].zfill(2), date_parts[2].zfill(2)))
    publication_date = parse_date(normalized_date[:10]) if raw_date else None
    month_text = fields.get("month", "")
    try:
        month = int(month_text)
        if not 1 <= month <= 12:
            month = None
    except ValueError:
        month = MONTHS.get(month_text.casefold().rstrip("."))
    day_text = fields.get("day", "")
    if raw_date:
        if len(date_parts) > 1 and date_parts[1].isdigit():
            parsed_month = int(date_parts[1])
            month = parsed_month if 1 <= parsed_month <= 12 else month
        elif len(date_parts) > 1:
            month = MONTHS.get(date_parts[1].casefold().rstrip("."), month)
        if len(date_parts) > 2 and date_parts[2].isdigit():
            day_text = date_parts[2]
    if publication_date:
        year = publication_date.year
        month = publication_date.month
    if not publication_date and year and month and day_text.isdigit():
        try:
            publication_date = date(year, month, int(day_text))
        except ValueError:
            publication_date = None
    return {
        "title": fields.get("title", ""),
        "authors": fields.get("author", fields.get("editor", "")),
        "publication_date": publication_date,
        "publication_year": year,
        "publication_month": month,
        "publication_day": publication_date.day if publication_date else None,
        "journal_name": fields.get("journal", fields.get("journaltitle", fields.get("booktitle", ""))),
        "volume": fields.get("volume", ""),
        "issue": fields.get("number", fields.get("issue", "")),
        "pages": fields.get("pages", ""),
        "doi": fields.get("doi", ""),
        "url": fields.get("url", ""),
    }
