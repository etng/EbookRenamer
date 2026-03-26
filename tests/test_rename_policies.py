import json
from pathlib import Path

import rename_books_by_meta as rbm


def test_clean_text_removes_separator_normalized_source_suffixes() -> None:
    raw = "Some_Book_z_library_sk,_1lib_sk,_z_lib_sk"
    assert rbm.clean_text(raw) == "Some_Book"


def test_author_from_filename_ignores_separator_normalized_source_noise() -> None:
    raw = "Example Book (z_library_sk,_1lib_sk,_z_lib_sk)"
    assert rbm.author_from_filename(raw) is None


def test_normalize_file_token_sanitizes_cjk_and_fullwidth_punctuation() -> None:
    raw = "逆向工程，你我都能變優秀的祕訣：全球頂尖創新者（Ron Friedman）"
    assert rbm.normalize_file_token(raw) == "逆向工程_你我都能變優秀的祕訣_全球頂尖創新者_Ron_Friedman"


def test_build_new_name_removes_book_title_quotes_from_epub_title() -> None:
    sample = Path("癸酉本_红楼梦_吴氏石头记_z_library_sk,_1lib_sk,_z_lib_sk.epub")
    new_name, _reason = rbm.build_new_name(
        sample,
        rbm.BookMeta(title="癸酉本《红楼梦》", author="吴氏石头记", date="2025"),
    )
    assert new_name == "癸酉本红楼梦-吴氏石头记-2025.epub"
    assert rbm.contains_suspicious_filename_chars(new_name) is False


def test_choose_best_title_rejects_pdf_probe_header_noise() -> None:
    sample = Path("mcs.pdf")
    title = rbm.choose_best_title(
        sample,
        "“mcs” — 2018/6/6 — 13:43 — page i — #1",
    )
    assert title == "mcs"


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
