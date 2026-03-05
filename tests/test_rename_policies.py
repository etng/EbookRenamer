import json
from pathlib import Path

import rename_books_by_meta as rbm


def test_rfc_pdf_is_unselected_by_default(tmp_path: Path, monkeypatch) -> None:
    index_file = tmp_path / "file_index.json"
    monkeypatch.setattr(rbm, "get_file_index_file", lambda: index_file)
    monkeypatch.setattr(
        rbm,
        "parse_pdf_meta",
        lambda _path, _opts=None: rbm.BookMeta(title="SOCKS Protocol Version 5", author="IETF", date="1996"),
    )

    sample = tmp_path / "rfc1928.txt.pdf"
    sample.write_bytes(b"dummy")

    plans = rbm.build_plans_for_directory(tmp_path, rbm.ScanOptions())
    assert len(plans) == 1
    assert plans[0].src == sample
    assert plans[0].selected is False
    assert plans[0].skip_reason == "rfc_like"
    assert plans[0].dst == sample.name


def test_weird_target_name_is_unselected_by_default(tmp_path: Path, monkeypatch) -> None:
    index_file = tmp_path / "file_index.json"
    monkeypatch.setattr(rbm, "get_file_index_file", lambda: index_file)
    monkeypatch.setattr(
        rbm,
        "parse_epub_meta",
        lambda _path: rbm.BookMeta(title="Bad�Title", author="A", date="2020"),
    )

    sample = tmp_path / "normal.epub"
    sample.write_bytes(b"dummy")

    plans = rbm.build_plans_for_directory(tmp_path, rbm.ScanOptions())
    assert len(plans) == 1
    assert plans[0].selected is False
    assert plans[0].skip_reason == "weird_chars"
    assert plans[0].dst == sample.name


def test_file_index_records_sha256_and_duplicate_counts(tmp_path: Path, monkeypatch) -> None:
    index_file = tmp_path / "file_index.json"
    monkeypatch.setattr(rbm, "get_file_index_file", lambda: index_file)
    monkeypatch.setattr(
        rbm,
        "parse_epub_meta",
        lambda path: rbm.BookMeta(title=path.stem, author="Tester", date="2024"),
    )

    file_a = tmp_path / "a.epub"
    file_b = tmp_path / "b.epub"
    payload = b"same content"
    file_a.write_bytes(payload)
    file_b.write_bytes(payload)

    plans = rbm.build_plans_for_directory(tmp_path, rbm.ScanOptions())
    assert len(plans) == 2
    assert index_file.exists()

    saved = json.loads(index_file.read_text(encoding="utf-8"))
    records = saved["records"]
    a_key = str(file_a.resolve())
    b_key = str(file_b.resolve())
    assert a_key in records
    assert b_key in records
    assert records[a_key]["sha256"]
    assert records[a_key]["sha256"] == records[b_key]["sha256"]

    for item in plans:
        assert item.reason.get("dup_count") == "2"
