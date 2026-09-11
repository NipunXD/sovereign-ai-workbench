"""The spreadsheet extractor, on the paths the API actually hands it."""

from __future__ import annotations

from pathlib import Path

from workbench.ingest.extractors.office import SpreadsheetExtractor


def test_a_csv_is_read_as_a_csv_even_with_no_extension_on_disk(tmp_path: Path) -> None:
    """Blobs are content-addressed, so the stored file has no suffix.

    Branching on the stored path's extension sent every CSV to the Excel
    reader. text/csv is in the API's allowed_mime list, so this was a file
    type the product advertised and could not ingest at all.
    """
    blob = tmp_path / "3f786850e387550fdab836ed7e6dc881de23001b"
    blob.write_text("Tag,Reading\nP-101A,6.4\nP-101B,2.1\n")

    ir = SpreadsheetExtractor().extract(blob, "doc_1", filename="P-101A-VIBRATION-2029.csv")

    assert ir.mime == "text/csv"
    text = "\n".join(b.text for page in ir.pages for b in page.blocks)
    assert "P-101A" in text and "6.4" in text


def test_an_xlsx_still_goes_to_the_excel_reader(tmp_path: Path) -> None:
    from openpyxl import Workbook

    blob = tmp_path / "da39a3ee5e6b4b0d3255bfef95601890afd80709"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Readings"
    sheet.append(["Tag", "Reading"])
    sheet.append(["V-1202", "9.85"])
    workbook.save(blob)

    ir = SpreadsheetExtractor().extract(blob, "doc_2", filename="readings.xlsx")

    assert ir.mime.endswith("spreadsheetml.sheet")
    assert "V-1202" in "\n".join(b.text for page in ir.pages for b in page.blocks)
