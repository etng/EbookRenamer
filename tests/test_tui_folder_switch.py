from pathlib import Path

from rename_books_by_meta import ScanOptions, build_plans_for_directory, render_textual_tui_preview


def test_folder_overlay_stays_open_and_can_confirm(tmp_path: Path) -> None:
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()

    # Dummy files are enough; parser failures are handled by fallback naming logic.
    (dir_a / "Alpha_Book-UnknownYear.epub").write_text("x", encoding="utf-8")
    (dir_b / "Beta_Book-UnknownYear.epub").write_text("x", encoding="utf-8")

    plans = build_plans_for_directory(dir_a, ScanOptions())
    app = render_textual_tui_preview(
        plans=plans,
        app_title="Ebook Renamer",
        update_url="https://example.invalid/latest.json",
        current_dir=dir_a,
        scan_options=ScanOptions(),
        run_app=False,
    )

    async def run_case() -> None:
        async with app.run_test() as pilot:
            await pilot.press("f")
            overlay = app.query_one("#folder_overlay")
            assert not overlay.has_class("hidden")

            # Simulate user choosing another folder and confirming with Enter.
            app.folder_candidate = dir_b
            await pilot.press("enter")
            assert app.current_dir == dir_b.resolve()

    import asyncio

    asyncio.run(run_case())
