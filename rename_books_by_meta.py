#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import locale
import os
import re
import shutil
import site
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

NOISE_PATTERNS = [
    r"z-library",
    r"1lib",
    r"z-lib",
    r"lib\.sk",
]

WINDOWS_FILENAME_LIMIT = 255
SAFE_FILENAME_LIMIT = 200
APP_VERSION = "0.2.1"
GITHUB_URL = "https://github.com/etng/ebook-renamer"
UPDATE_METADATA_URL = "https://github.com/etng/EbookRenamer/releases/latest/download/latest.json"
APP_CONFIG_DIR_NAME = "ebook-renamer"
APP_CONFIG_FILE_NAME = "config.json"

LANG_DISPLAY_NAMES = {
    "en": "English",
    "zh_CN": "简体中文",
    "zh_TW": "繁體中文",
    "ja": "日本語",
    "vi": "Tiếng Việt",
}
DEFAULT_LANG = "en"


@dataclass
class BookMeta:
    title: str | None = None
    author: str | None = None
    date: str | None = None
    modified: str | None = None


@dataclass
class RenamePlan:
    src: Path
    dst: str
    reason: dict[str, str]


@dataclass
class ScanOptions:
    allow_ocr: bool = False
    allow_online: bool = False


def resource_base_dir() -> Path:
    if getattr(sys, "_MEIPASS", None):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def get_locales_dir() -> Path:
    return resource_base_dir() / "locales"


def get_config_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_CONFIG_DIR_NAME
    if sys.platform.startswith("win"):
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / APP_CONFIG_DIR_NAME
    return Path.home() / ".config" / APP_CONFIG_DIR_NAME


def get_config_file() -> Path:
    return get_config_dir() / APP_CONFIG_FILE_NAME


def load_user_config() -> dict:
    cfg_file = get_config_file()
    if not cfg_file.exists():
        return {}
    try:
        return json.loads(cfg_file.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_user_config(cfg: dict) -> None:
    cfg_dir = get_config_dir()
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = get_config_file()
    cfg_file.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_lang_code(raw: str | None) -> str:
    if not raw:
        return DEFAULT_LANG
    code = raw.strip().replace("-", "_")
    code = code.split(".")[0]
    code_l = code.lower()
    if code_l.startswith("zh_tw") or code_l.startswith("zh_hk") or code_l.startswith("zh_mo"):
        return "zh_TW"
    if code_l.startswith("zh"):
        return "zh_CN"
    if code_l.startswith("ja"):
        return "ja"
    if code_l.startswith("vi"):
        return "vi"
    if code_l.startswith("en"):
        return "en"
    return DEFAULT_LANG


def detect_system_language() -> str:
    env_lang = os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG")
    if env_lang:
        return normalize_lang_code(env_lang)
    try:
        loc = locale.getlocale()[0]
        if loc:
            return normalize_lang_code(loc)
    except Exception:
        pass
    return DEFAULT_LANG


def load_language_packs() -> dict[str, dict[str, str]]:
    packs: dict[str, dict[str, str]] = {}
    loc_dir = get_locales_dir()
    if not loc_dir.exists():
        return packs

    for code in LANG_DISPLAY_NAMES.keys():
        p = loc_dir / f"{code}.json"
        if not p.exists():
            continue
        try:
            packs[code] = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
    return packs


def parse_semver(version: str) -> tuple[int, int, int] | None:
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", version.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def fetch_latest_metadata(update_url: str) -> tuple[dict | None, str | None]:
    try:
        with urllib.request.urlopen(update_url, timeout=8) as resp:
            if resp.status != 200:
                return None, f"HTTP {resp.status}"
            payload = resp.read().decode("utf-8", errors="replace")
        data = json.loads(payload)
        if not isinstance(data, dict):
            return None, "invalid metadata format"
        return data, None
    except urllib.error.HTTPError as e:
        return None, f"HTTPError {e.code}"
    except urllib.error.URLError as e:
        return None, f"URLError {e.reason}"
    except Exception as e:
        return None, str(e)


def check_update_once(update_url: str, current_version: str) -> tuple[bool | None, str]:
    data, err = fetch_latest_metadata(update_url)
    if err:
        return None, f"check failed: {err}"

    latest = str(data.get("version", "")).strip()
    if not latest:
        latest = str(data.get("tag", "")).strip().lstrip("v")
    latest_release_url = str(data.get("release_url", "")).strip()

    cur_sem = parse_semver(current_version)
    latest_sem = parse_semver(latest)
    if cur_sem is None or latest_sem is None:
        return None, f"invalid semver (current={current_version}, latest={latest})"

    if latest_sem > cur_sem:
        suffix = f"\nrelease: {latest_release_url}" if latest_release_url else ""
        return True, f"new version available: {latest} (current: {current_version}){suffix}"
    return False, f"up to date: {current_version}"


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=False)


def detect_pkg_manager() -> str | None:
    for candidate in ["brew", "apt-get", "dnf", "yum", "pacman", "zypper", "choco", "winget"]:
        if shutil.which(candidate):
            return candidate
    return None


def install_command(command_name: str) -> bool:
    manager = detect_pkg_manager()
    if manager is None:
        print(f"[WARN] {command_name} not found and no supported package manager detected.")
        return False

    install_map = {
        "pdfinfo": {
            "brew": [["brew", "install", "poppler"]],
            "apt-get": [["sudo", "apt-get", "update"], ["sudo", "apt-get", "install", "-y", "poppler-utils"]],
            "dnf": [["sudo", "dnf", "install", "-y", "poppler-utils"]],
            "yum": [["sudo", "yum", "install", "-y", "poppler-utils"]],
            "pacman": [["sudo", "pacman", "-Sy", "--noconfirm", "poppler"]],
            "zypper": [["sudo", "zypper", "install", "-y", "poppler-tools"]],
            "choco": [["choco", "install", "poppler", "-y"]],
            "winget": [["winget", "install", "--id", "oschwartz10612.poppler", "-e"]],
        }
    }

    recipe = install_map.get(command_name, {}).get(manager)
    if not recipe:
        print(f"[WARN] No install recipe for {command_name} via {manager}.")
        return False

    print(f"[INFO] {command_name} not found. Trying to install via {manager} ...")
    for cmd in recipe:
        print("[INFO] $", " ".join(cmd))
        proc = run(cmd)
        if proc.returncode != 0:
            if proc.stderr:
                print(proc.stderr.strip())
            if proc.stdout:
                print(proc.stdout.strip())
            print(f"[WARN] Install step failed: {' '.join(cmd)}")
            return False

    ok = shutil.which(command_name) is not None
    if ok:
        print(f"[INFO] {command_name} installed successfully.")
    else:
        print(f"[WARN] Tried installing {command_name}, but it is still unavailable.")
    return ok


def ensure_command(command_name: str) -> bool:
    if shutil.which(command_name):
        return True
    return install_command(command_name)


def has_module(module_name: str) -> bool:
    if importlib.util.find_spec(module_name) is not None:
        return True
    enable_user_site_path()
    importlib.invalidate_caches()
    return importlib.util.find_spec(module_name) is not None


def can_import_module(module_name: str) -> bool:
    try:
        __import__(module_name)
        return True
    except Exception:
        return False


def enable_user_site_path() -> None:
    candidates: list[str] = []
    try:
        candidates.append(site.getusersitepackages())
    except Exception:
        pass
    candidates.append(
        os.path.expanduser(
            f"~/Library/Python/{sys.version_info.major}.{sys.version_info.minor}/lib/python/site-packages"
        )
    )
    for p in candidates:
        if p and os.path.isdir(p) and p not in sys.path:
            sys.path.append(p)


def ensure_python_module(module_name: str, package_name: str | None = None) -> bool:
    if has_module(module_name):
        return True
    pkg = package_name or module_name
    print(f"[INFO] Python module '{module_name}' not found. Trying to install '{pkg}' ...")
    install_attempts = [
        [sys.executable, "-m", "pip", "install", "--user", pkg],
    ]

    # On Homebrew/externally managed Python (PEP 668), pip may require this flag.
    install_attempts.append(
        [sys.executable, "-m", "pip", "install", "--break-system-packages", "--user", pkg]
    )

    last_stdout = ""
    last_stderr = ""
    for idx, cmd in enumerate(install_attempts):
        proc = run(cmd)
        last_stdout = proc.stdout or ""
        last_stderr = proc.stderr or ""
        if proc.returncode == 0:
            break

        if idx == 0 and "externally-managed-environment" in (last_stderr + last_stdout):
            print("[INFO] Detected externally-managed Python; retrying with --break-system-packages.")
            continue

        if last_stderr:
            print(last_stderr.strip())
        if last_stdout:
            print(last_stdout.strip())
        print(f"[WARN] Failed to install python package: {pkg}")
        return False

    enable_user_site_path()
    importlib.invalidate_caches()
    if not has_module(module_name):
        if last_stderr:
            print(last_stderr.strip())
        if last_stdout:
            print(last_stdout.strip())
        try_import = run([sys.executable, "-c", f"import {module_name}; print('ok')"])
        if try_import.returncode == 0:
            print(
                f"[INFO] Module '{module_name}' is importable in a fresh interpreter; "
                "current process path was refreshed."
            )
            return True
        print(
            f"[WARN] Installed package '{pkg}' but module '{module_name}' is still unavailable. "
            f"python={sys.executable}"
        )
        return False
    return has_module(module_name)


def extract_year(text: str | None) -> str | None:
    if not text:
        return None
    m = re.search(r"(19\d{2}|20\d{2}|2100)", text)
    return m.group(1) if m else None


def clean_text(text: str) -> str:
    value = text
    for p in NOISE_PATTERNS:
        value = re.sub(p, "", value, flags=re.IGNORECASE)
    # Drop placeholder tokens from legacy filenames.
    value = re.sub(r"\bUnknown(?:Year|Author)\b", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\([^)]*\)", "", value)
    value = re.sub(r"\s+", " ", value).strip(" ._-")
    return value


def normalize_file_token(text: str) -> str:
    value = text.strip()
    value = re.sub(r"[\\/:*?\"<>|]", " ", value)
    value = re.sub(r"[.,;()\[\]{}]", " ", value)
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_ .")
    return value or "Unknown"


def abbreviate_title_phrases(title: str) -> str:
    value = title

    # Normalize common typo first.
    value = re.sub(r"\bEdtion\b", "Edition", value, flags=re.IGNORECASE)

    # 2nd Edition -> 2e, 3rd Edition -> 3e ...
    value = re.sub(
        r"\b(\d+)\s*(st|nd|rd|th)\s+Edition\b",
        lambda m: f"{m.group(1)}e",
        value,
        flags=re.IGNORECASE,
    )
    # Second Edition -> 2e (common written ordinals)
    word_ordinal = {
        "first": "1e",
        "second": "2e",
        "third": "3e",
        "fourth": "4e",
        "fifth": "5e",
        "sixth": "6e",
        "seventh": "7e",
        "eighth": "8e",
        "ninth": "9e",
        "tenth": "10e",
    }
    for k, v in word_ordinal.items():
        value = re.sub(rf"\b{k}\s+Edition\b", v, value, flags=re.IGNORECASE)

    # Other concise markers.
    replacements = [
        (r"\bRevised\s+Edition\b", "RevEd"),
        (r"\bUpdated\s+Edition\b", "UpdEd"),
        (r"\bInternational\s+Edition\b", "IntlEd"),
        (r"\bCollector'?s\s+Edition\b", "CollEd"),
        (r"\bSpecial\s+Edition\b", "SpecEd"),
        (r"\bStudent\s+Edition\b", "StuEd"),
        (r"\bEdition\b", "Ed"),
        (r"\bRelease\b", "Rel"),
        (r"\bVolume\b", "Vol"),
        (r"\bVol\.\b", "Vol"),
        (r"\bPart\b", "Pt"),
        (r"\bNumber\b", "No"),
    ]
    for pattern, repl in replacements:
        value = re.sub(pattern, repl, value, flags=re.IGNORECASE)

    value = re.sub(r"\s+", " ", value).strip()
    return value


def main_title_only(title: str) -> str:
    t = clean_text(title)
    t = re.sub(r"\s+A\s+Novel\s+about\s+.*$", "", t, flags=re.IGNORECASE)
    for sep in [" -- ", " - ", ": "]:
        if sep in t:
            head, tail = t.split(sep, 1)
            # Keep edition info if it appears in tail and head is too generic.
            if re.search(r"\b\d+(st|nd|rd|th)\s+edition\b", tail, flags=re.IGNORECASE):
                t = f"{head} {re.search(r'\b\d+(st|nd|rd|th)\s+edition\b', tail, flags=re.IGNORECASE).group(0)}"
            else:
                t = head
            break
    return t.strip()


def looks_like_bad_title(title: str | None) -> bool:
    if not title:
        return True
    t = title.strip()
    if len(t) < 3:
        return True
    if re.fullmatch(r"B[0-9A-Z]{9}(?:\s*\(.*\))?", t):
        return True
    return False


def title_word_count(text: str | None) -> int:
    if not text:
        return 0
    return len(re.findall(r"[A-Za-z0-9]+", text))


def normalize_title_for_compare(text: str) -> str:
    value = text.lower()
    value = re.sub(r"[_\-]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def has_edition_marker(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:\d+e|\d+(?:st|nd|rd|th)\s+edition|first\s+edition|second\s+edition|third\s+edition)\b",
            text,
            flags=re.IGNORECASE,
        )
    )


def choose_best_title(path: Path, meta_title: str | None) -> str:
    fallback = fallback_title_from_filename(path)
    if looks_like_bad_title(meta_title):
        return fallback

    meta_main = main_title_only(meta_title or "")
    fallback_main = main_title_only(fallback)
    meta_norm = normalize_title_for_compare(meta_main)
    fallback_norm = normalize_title_for_compare(fallback_main)

    # Prefer richer filename title when it carries explicit edition markers
    # and metadata title does not.
    if has_edition_marker(fallback_main) and not has_edition_marker(meta_main):
        if fallback_norm.startswith(meta_norm):
            return fallback_main

    # If metadata title is too short but filename contains a richer title that
    # starts with the same phrase, prefer the richer one.
    if (
        title_word_count(meta_main) <= 2
        and title_word_count(fallback_main) >= 4
        and fallback_norm.startswith(meta_norm)
    ):
        return fallback_main

    return meta_main


def split_first_author(author_text: str | None) -> str | None:
    if not author_text:
        return None
    value = author_text.strip().strip(";")
    value = re.sub(r"\b(PhD|M\.D\.|MD)\b", "", value, flags=re.IGNORECASE).strip(" ,;")

    m = re.split(r"\s*(?:,|;| and | & )\s*", value)
    if m:
        first = m[0].strip()
    else:
        first = value

    # Handle "Last, First"
    if "," in first:
        parts = [p.strip() for p in first.split(",") if p.strip()]
        if len(parts) >= 2:
            first = f"{parts[1]} {parts[0]}"

    if not first:
        return None
    return re.sub(r"\s+", " ", first)


def author_from_filename(name_stem: str) -> str | None:
    groups = re.findall(r"\(([^)]{2,})\)", name_stem)
    for g in groups:
        if re.search(r"z-library|1lib|z-lib|lib\.sk", g, flags=re.IGNORECASE):
            continue
        raw = g.strip()
        if not raw:
            continue

        if "," in raw or ";" in raw:
            candidate = re.split(r"\s*(?:,|;)\s*", raw)[0].strip()
            if candidate:
                return candidate

        tokens = raw.split()
        if len(tokens) >= 2:
            return f"{tokens[0]} {tokens[1]}"
        return raw

    # Fallback: infer from last non-year segment after hyphen splits.
    parts = [p.strip() for p in re.split(r"-+", name_stem) if p.strip()]
    if parts:
        idx = len(parts) - 1
        if re.fullmatch(r"(?:19\d{2}|20\d{2}|2100|UnknownYear)", parts[idx], flags=re.IGNORECASE):
            idx -= 1
        if idx >= 0:
            candidate = re.sub(r"[_\s]+", " ", parts[idx]).strip()
            tokens = [t for t in candidate.split() if t]
            if 1 <= len(tokens) <= 3:
                if all(re.fullmatch(r"[A-Za-z][A-Za-z.'-]*", t) for t in tokens):
                    if len(tokens) >= 2 and all(t[0].isupper() for t in tokens):
                        return " ".join(tokens)
                    if len(tokens) == 1 and len(tokens[0]) >= 4 and tokens[0][0].isupper():
                        return tokens[0]
    return None


def strip_author_from_title(title: str, author_text: str | None, year: str | None = None) -> str:
    if not title or not author_text:
        return title

    author_token = normalize_file_token(author_text)
    if not author_token:
        return title

    # Match common title tails/heads that duplicate author info from filenames.
    author_pat = re.escape(author_token).replace(r"\_", r"[ _-]+")
    sep = r"[\s_\-–—,:|/]*"
    year_pat = re.escape(year) if year else r"(?:19\d{2}|20\d{2}|2100)"
    patterns = [
        rf"(?i){sep}{author_pat}(?:{sep}{year_pat})?\s*$",
        rf"(?i){sep}{year_pat}{sep}{author_pat}\s*$",
        rf"(?i)^\s*{author_pat}{sep}",
    ]

    cleaned = title
    for pat in patterns:
        cleaned = re.sub(pat, "", cleaned).strip(" _-–—,|:/")

    if not cleaned:
        return title
    return cleaned


def extract_pdf_first_page_text(path: Path) -> str:
    cmds: list[list[str]] = []
    if ensure_command("pdftotext"):
        cmds.append(["pdftotext", "-f", "1", "-l", "1", str(path), "-"])
    if shutil.which("mutool"):
        cmds.append(["mutool", "draw", "-F", "txt", "-i", str(path), "1"])

    for cmd in cmds:
        proc = run(cmd)
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout
    return ""


def is_likely_author_line(line: str) -> bool:
    lowered = line.lower()
    if "@" in line:
        return False
    if "based on research" in lowered or "collaboration" in lowered:
        return False
    words = re.findall(r"[A-Za-z][A-Za-z.\-']*", line)
    if len(words) < 2 or len(words) > 12:
        return False
    upper_words = sum(1 for w in words if w[:1].isupper())
    if upper_words < 2:
        return False
    return ("," in line) or (" and " in lowered) or (len(words) <= 4)


def parse_pdf_probe_meta_from_text(text: str) -> BookMeta:
    lines: list[str] = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if line:
            lines.append(line)

    if not lines:
        return BookMeta()

    date_text: str | None = None
    for line in lines[:20]:
        if line.lower().startswith("arxiv:"):
            date_text = line
            break

    title: str | None = None
    title_idx = -1
    for idx, line in enumerate(lines[:60]):
        lowered = line.lower()
        if lowered.startswith(("arxiv:", "contents", "preface", "abstract")):
            continue
        if "@" in line:
            continue
        if re.fullmatch(r"[ivxlcdm]+", lowered):
            continue
        words = line.split()
        if len(words) < 3 or len(words) > 24:
            continue
        if len(line) < 12:
            continue
        if is_likely_author_line(line):
            continue
        title = line
        title_idx = idx
        if idx + 1 < len(lines):
            nxt = lines[idx + 1]
            nxt_lower = nxt.lower()
            if (
                len(nxt.split()) >= 3
                and len(nxt) <= 140
                and not is_likely_author_line(nxt)
                and not nxt_lower.startswith(("arxiv:", "contents", "preface", "abstract", "based on "))
            ):
                joiner = ": " if not title.endswith((".", ":", "-", "?", "!")) else " "
                title = f"{title}{joiner}{nxt}"
        break

    author: str | None = None
    start = title_idx + 1 if title_idx >= 0 else 0
    for line in lines[start : start + 18]:
        if is_likely_author_line(line):
            author = line
            break

    return BookMeta(
        title=title or None,
        author=author or None,
        date=date_text or None,
    )


def parse_pdf_text_probe(path: Path, options: ScanOptions | None = None) -> BookMeta:
    _ = options
    text = extract_pdf_first_page_text(path)
    if not text:
        return BookMeta()
    return parse_pdf_probe_meta_from_text(text)


def parse_pdf_meta(path: Path, options: ScanOptions | None = None) -> BookMeta:
    if not ensure_command("pdfinfo"):
        raise RuntimeError("pdfinfo unavailable and auto-install failed")

    proc = run(["pdfinfo", str(path)])
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"pdfinfo failed for {path}")

    data: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip()

    meta = BookMeta(
        title=data.get("Title") or None,
        author=data.get("Author") or None,
        date=data.get("CreationDate") or None,
        modified=data.get("ModDate") or None,
    )
    if not meta.title or not meta.author or not meta.date:
        probe = parse_pdf_text_probe(path, options)
        if not meta.title and probe.title:
            meta.title = probe.title
        if not meta.author and probe.author:
            meta.author = probe.author
        if not meta.date and probe.date:
            meta.date = probe.date
    return meta


def find_opf_path(epub_path: Path) -> str:
    with zipfile.ZipFile(epub_path) as zf:
        with zf.open("META-INF/container.xml") as f:
            tree = ET.parse(f)
    root = tree.getroot()
    for elem in root.iter():
        if elem.tag.endswith("rootfile"):
            full_path = elem.attrib.get("full-path")
            if full_path:
                return full_path
    raise RuntimeError(f"Cannot locate OPF in {epub_path.name}")


def parse_epub_meta(path: Path) -> BookMeta:
    opf_path = find_opf_path(path)

    with zipfile.ZipFile(path) as zf:
        with zf.open(opf_path) as f:
            tree = ET.parse(f)

    root = tree.getroot()

    titles: list[str] = []
    creators: list[str] = []
    dates: list[str] = []
    modified: str | None = None

    for elem in root.iter():
        tag = elem.tag.lower()
        text = (elem.text or "").strip()
        if tag.endswith("title") and text:
            titles.append(text)
        elif tag.endswith("creator") and text:
            creators.append(text)
        elif tag.endswith("date") and text:
            dates.append(text)
        elif tag.endswith("meta"):
            prop = elem.attrib.get("property", "")
            if prop == "dcterms:modified" and text:
                modified = text

    return BookMeta(
        title=titles[0] if titles else None,
        author=creators[0] if creators else None,
        date=dates[0] if dates else None,
        modified=modified,
    )


def fallback_title_from_filename(path: Path) -> str:
    stem = clean_text(path.stem)
    # Remove likely author/source tails in parentheses already done by clean_text.
    return main_title_only(stem)


def build_new_name(path: Path, meta: BookMeta) -> tuple[str, dict[str, str]]:
    author_raw = split_first_author(meta.author)
    if not author_raw:
        author_raw = author_from_filename(path.stem) or ""
    author = normalize_file_token(author_raw) if author_raw else ""

    year = (
        extract_year(meta.date)
        or extract_year(meta.modified)
        or extract_year(path.stem)
        or ""
    )

    title_raw = choose_best_title(path, meta.title)
    title_dedup = strip_author_from_title(title_raw, author_raw, year)
    title_abbr = abbreviate_title_phrases(title_dedup)
    title = normalize_file_token(title_abbr)

    stem_parts = [title]
    if author:
        stem_parts.append(author)
    if year:
        stem_parts.append(year)
    new_name = f"{'-'.join(stem_parts)}{path.suffix.lower()}"
    reason = {
        "title": title_dedup or "fallback(filename)",
        "title_final": title,
        "title_len": str(len(title)),
        "author": author_raw,
        "year": year,
        "name_len": str(len(new_name)),
    }
    return new_name, reason


def unique_name(target_dir: Path, desired_name: str) -> str:
    candidate = desired_name
    base = Path(desired_name).stem
    suffix = Path(desired_name).suffix
    n = 2
    while (target_dir / candidate).exists():
        candidate = f"{base}-{n}{suffix}"
        n += 1
    return candidate


def unique_name_with_reserved(target_dir: Path, desired_name: str, reserved: set[str]) -> str:
    candidate = desired_name
    base = Path(desired_name).stem
    suffix = Path(desired_name).suffix
    n = 2
    while (target_dir / candidate).exists() or candidate.casefold() in reserved:
        candidate = f"{base}-{n}{suffix}"
        n += 1
    return candidate


def collect_files(dir_path: Path) -> Iterable[Path]:
    for p in sorted(dir_path.iterdir()):
        if p.is_file() and p.suffix.lower() in {".epub", ".pdf"}:
            yield p


def detect_gui_backend(try_install: bool = False) -> str | None:
    # Priority: Qt > Tk
    candidates: list[tuple[str, str | None]] = [
        ("PySide6", "PySide6"),
        ("PyQt6", "PyQt6"),
        ("tkinter", None),
    ]
    for module_name, pkg_name in candidates:
        if has_module(module_name) and can_import_module(module_name):
            return module_name
    if try_install:
        for module_name, pkg_name in candidates:
            if module_name == "tkinter":
                continue
            if ensure_python_module(module_name, pkg_name):
                return module_name
    return None


def detect_tui_backend(try_install: bool = False) -> str | None:
    # Priority: Textual > Rich
    candidates: list[tuple[str, str]] = [
        ("textual", "textual"),
        ("rich", "rich"),
    ]
    for module_name, _ in candidates:
        if has_module(module_name):
            return module_name
    if try_install:
        for module_name, pkg_name in candidates:
            if ensure_python_module(module_name, pkg_name):
                return module_name
    return None


def can_show_gui() -> bool:
    if sys.platform in {"darwin", "win32"}:
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def calc_title_len_from_target(target_name: str) -> int:
    stem = Path(target_name).stem
    title_part = stem.split("-", 1)[0] if "-" in stem else stem
    return len(title_part)


def calc_length_note(name_len: int, same_name: bool) -> str:
    if name_len > WINDOWS_FILENAME_LIMIT:
        return ">255"
    if name_len > SAFE_FILENAME_LIMIT:
        return ">200"
    if same_name:
        return "same"
    return ""


def validate_target_filename(name: str) -> str | None:
    value = name.strip()
    if not value:
        return "target_empty"
    if "/" in value or "\\" in value:
        return "target_path_separators"
    if re.search(r'[:*?"<>|]', value):
        return "target_windows_illegal_chars"
    if value in {".", ".."}:
        return "target_invalid_dot"
    return None


def build_plans_for_directory(target: Path, options: ScanOptions | None = None) -> list[RenamePlan]:
    plans: list[RenamePlan] = []
    reserved_targets: set[str] = set()
    for file_path in collect_files(target):
        try:
            if file_path.suffix.lower() == ".pdf":
                meta = parse_pdf_meta(file_path, options)
            else:
                meta = parse_epub_meta(file_path)
        except Exception as e:
            print(f"[WARN] Metadata parse failed for {file_path.name}: {e}")
            meta = BookMeta()

        desired, reason = build_new_name(file_path, meta)
        if desired == file_path.name:
            final_name = desired
        else:
            final_name = unique_name_with_reserved(target, desired, reserved_targets)
        reserved_targets.add(final_name.casefold())
        plans.append(RenamePlan(src=file_path, dst=final_name, reason=reason))
    return plans


def apply_rename_pairs(rename_pairs: list[tuple[Path, str]]) -> tuple[int, str | None]:
    if not rename_pairs:
        return 0, None

    src_paths = [src for src, _ in rename_pairs]
    src_set = set(src_paths)
    target_dir = src_paths[0].parent

    seen: set[str] = set()
    for src, dst_name in rename_pairs:
        key = dst_name.casefold()
        if key in seen:
            return 0, f"Duplicate target filename: {dst_name}"
        seen.add(key)
        dst_path = target_dir / dst_name
        if dst_path.exists() and dst_path not in src_set:
            return 0, f"Target already exists: {dst_name}"

    temp_moves: list[tuple[Path, Path]] = []
    final_moves: list[tuple[Path, Path]] = []
    try:
        for idx, (src, dst_name) in enumerate(rename_pairs):
            if src.name == dst_name:
                continue
            tmp = src.parent / f".rename_tmp_{os.getpid()}_{idx}_{src.name}"
            while tmp.exists():
                tmp = src.parent / f".rename_tmp_{os.getpid()}_{idx}_{os.urandom(2).hex()}_{src.name}"
            src.rename(tmp)
            temp_moves.append((tmp, src))
            final_moves.append((tmp, src.parent / dst_name))

        changed = 0
        for tmp, dst in final_moves:
            tmp.rename(dst)
            changed += 1
        return changed, None
    except Exception as e:
        for tmp, original in reversed(temp_moves):
            try:
                if tmp.exists():
                    tmp.rename(original)
            except Exception:
                pass
        return 0, str(e)


def render_cli_preview(plans: list[RenamePlan]) -> None:
    print("\n=== Rename Preview ===")
    for item in plans:
        src = item.src
        dst = item.dst
        reason = item.reason
        marker = "(same)" if src.name == dst else ""
        length_note = ""
        try:
            dst_len = int(reason["name_len"])
        except ValueError:
            dst_len = len(dst)
        if dst_len > WINDOWS_FILENAME_LIMIT:
            length_note = " [WARN: >255]"
        elif dst_len > SAFE_FILENAME_LIMIT:
            length_note = " [WARN: >200]"
        print(f"- {src.name}")
        print(f"  -> {dst} {marker}{length_note}")
        print(
            "     "
            f"title={reason['title']} | title_len={reason['title_len']} | "
            f"author={reason['author']} | year={reason['year']} | name_len={reason['name_len']}"
        )


def render_rich_tui_preview(plans: list[RenamePlan]) -> None:
    from rich.console import Console
    from rich.table import Table

    table = Table(title="Rename Preview")
    table.add_column("Current", style="cyan")
    table.add_column("Target", style="green")
    table.add_column("Title Len", justify="right")
    table.add_column("Name Len", justify="right")
    table.add_column("Note", style="yellow")

    for item in plans:
        note = ""
        try:
            dst_len = int(item.reason["name_len"])
        except ValueError:
            dst_len = len(item.dst)
        if dst_len > WINDOWS_FILENAME_LIMIT:
            note = ">255"
        elif dst_len > SAFE_FILENAME_LIMIT:
            note = ">200"
        elif item.src.name == item.dst:
            note = "same"
        table.add_row(
            item.src.name,
            item.dst,
            item.reason["title_len"],
            item.reason["name_len"],
            note,
        )

    Console().print(table)


def render_textual_tui_preview(
    plans: list[RenamePlan],
    app_title: str,
    update_url: str,
    current_dir: Path,
    scan_options: ScanOptions,
    run_app: bool = True,
):
    from textual.app import App, ComposeResult
    from textual.containers import Container, Horizontal, Vertical, VerticalScroll
    from textual.widgets import Button, DataTable, Input, Static, Tree

    all_packs = load_language_packs()
    fallback_pack = all_packs.get(DEFAULT_LANG, {})

    class PreviewApp(App):
        CSS = """
        #title { height: auto; padding: 1 1; text-style: bold; }
        #path { height: auto; padding: 0 1 1 1; color: $text-muted; }
        DataTable { height: 1fr; }
        #actions { height: auto; align: center middle; padding: 1 1; }
        #actions Button { margin: 0 1; width: auto; min-width: 8; }
        .overlay {
            layer: overlay;
            width: 100%;
            height: 100%;
            align: center middle;
            background: $background 75%;
        }
        .overlay_box {
            width: auto;
            min-width: 48;
            max-width: 90;
            height: auto;
            max-height: 90%;
            padding: 1 2;
            border: round $panel;
            background: $surface;
        }
        .message_box { min-width: 56; max-width: 96; }
        .edit_box { min-width: 44; max-width: 70; }
        .folder_box { min-width: 60; max-width: 100; max-height: 90%; }
        .hidden { display: none; }
        #message_title { padding: 0 0 1 0; text-style: bold; color: $text; }
        #message_scroll { height: auto; max-height: 20; border: round $panel; }
        #message_text { padding: 0 1; }
        #edit_label { padding: 0 0 1 0; }
        #edit_input { margin: 0 0 1 0; }
        #folder_tree { height: 20; border: round $panel; margin: 0 0 1 0; }
        #message_actions, #edit_actions { align: center middle; }
        #folder_actions { align: center middle; }
        #message_actions Button, #edit_actions Button, #folder_actions Button { width: auto; min-width: 8; margin: 0 1; }
        """
        BINDINGS = [
            ("f", "choose_folder", "Folder"),
            ("e", "edit_target", "Edit"),
            ("a", "apply_rename", "Apply"),
            ("u", "check_update", "Update"),
            ("l", "switch_language", "Language"),
            ("i", "show_about", "About"),
            ("h", "show_help", "Help"),
            ("q", "quit", "Exit"),
        ]

        def __init__(self) -> None:
            super().__init__()
            self.packs = all_packs
            self.cfg = load_user_config()
            self.lang_code = self.resolve_initial_lang()
            self.current_dir = current_dir
            self.plans = list(plans)
            self.working_targets = [item.dst for item in self.plans]
            self.editing_row = 0
            self.folder_candidate: Path | None = None

        def t(self, key: str, **kwargs: object) -> str:
            pack = self.packs.get(self.lang_code, {})
            template = pack.get(key) or fallback_pack.get(key) or key
            try:
                return template.format(**kwargs)
            except Exception:
                return template

        def resolve_initial_lang(self) -> str:
            user_lang = normalize_lang_code(str(self.cfg.get("lang", "")))
            if user_lang in self.packs:
                return user_lang
            sys_lang = detect_system_language()
            if sys_lang in self.packs:
                return sys_lang
            return DEFAULT_LANG

        def next_lang(self) -> str:
            codes = [c for c in LANG_DISPLAY_NAMES.keys() if c in self.packs]
            if not codes:
                return DEFAULT_LANG
            if self.lang_code not in codes:
                return codes[0]
            idx = codes.index(self.lang_code)
            return codes[(idx + 1) % len(codes)]

        def compose(self) -> ComposeResult:
            yield Static("", id="title")
            yield Static("", id="path")
            yield DataTable(id="table")
            with Horizontal(id="actions"):
                yield Button("", id="folder")
                yield Button("", id="edit_target")
                yield Button("", id="apply")
                yield Button("", id="check_update")
                yield Button("", id="language")
                yield Button("", id="about")
                yield Button("", id="help")
                yield Button("Exit", id="exit")
            with Container(classes="overlay hidden", id="message_overlay"):
                with Vertical(classes="overlay_box message_box"):
                    yield Static("", id="message_title")
                    with VerticalScroll(id="message_scroll"):
                        yield Static("", id="message_text")
                    with Horizontal(id="message_actions"):
                        yield Button("OK", id="message_ok")
            with Container(classes="overlay hidden", id="edit_overlay"):
                with Vertical(classes="overlay_box edit_box"):
                    yield Static("", id="edit_label")
                    yield Input("", id="edit_input")
                    with Horizontal(id="edit_actions"):
                        yield Button("", id="edit_save")
                        yield Button("", id="edit_cancel")
            with Container(classes="overlay hidden", id="folder_overlay"):
                with Vertical(classes="overlay_box folder_box"):
                    yield Static("", id="folder_label")
                    yield Tree("/", id="folder_tree")
                    with Horizontal(id="folder_actions"):
                        yield Button("", id="folder_open")
                        yield Button("", id="folder_cancel")

        def refresh_table(self) -> None:
            table = self.query_one("#table", DataTable)
            row_count_before = table.row_count
            current_row = table.cursor_row if row_count_before > 0 else 0
            table.clear(columns=True)
            table.cursor_type = "row"
            table.add_columns(
                self.t("table_current"),
                self.t("table_target"),
                self.t("table_title_len"),
                self.t("table_name_len"),
                self.t("table_note"),
            )
            for idx, item in enumerate(self.plans):
                dst_name = self.working_targets[idx]
                note = ""
                try:
                    dst_len = len(dst_name)
                except ValueError:
                    dst_len = len(dst_name)
                if dst_len > WINDOWS_FILENAME_LIMIT:
                    note = ">255"
                elif dst_len > SAFE_FILENAME_LIMIT:
                    note = ">200"
                elif item.src.name == dst_name:
                    note = self.t("note_same")
                table.add_row(
                    item.src.name,
                    dst_name,
                    str(calc_title_len_from_target(dst_name)),
                    str(len(dst_name)),
                    note,
                )
            if table.row_count > 0:
                safe_row = min(max(current_row, 0), table.row_count - 1)
                table.move_cursor(row=safe_row, column=1)

        def show_message(self, title: str, message: str) -> None:
            self.query_one("#message_title", Static).update(title)
            self.query_one("#message_text", Static).update(message)
            self.query_one("#message_overlay", Container).remove_class("hidden")

        def hide_message(self) -> None:
            self.query_one("#message_overlay", Container).add_class("hidden")

        def show_edit_overlay(self) -> None:
            table = self.query_one("#table", DataTable)
            if table.row_count == 0:
                self.show_message(self.t("dialog_noop_title"), self.t("dialog_noop_body"))
                return
            self.editing_row = min(max(table.cursor_row, 0), table.row_count - 1)
            current_name = self.working_targets[self.editing_row]
            self.query_one("#edit_label", Static).update(
                f"{self.t('table_target')} (row {self.editing_row + 1})"
            )
            edit_input = self.query_one("#edit_input", Input)
            edit_input.value = current_name
            self.query_one("#edit_overlay", Container).remove_class("hidden")
            edit_input.focus()

        def hide_edit_overlay(self) -> None:
            self.query_one("#edit_overlay", Container).add_class("hidden")

        def show_folder_overlay(self) -> None:
            self.query_one("#folder_label", Static).update(self.t("dialog_folder_select_title"))
            self.query_one("#folder_overlay", Container).remove_class("hidden")
            self.folder_candidate = self.current_dir
            self.prepare_folder_tree()

        def hide_folder_overlay(self) -> None:
            self.query_one("#folder_overlay", Container).add_class("hidden")
            self.folder_candidate = None

        def add_placeholder(self, node: object) -> None:
            node.add("...", data="__placeholder__", allow_expand=False)

        def iter_subdirs(self, path: Path) -> list[Path]:
            children: list[Path] = []
            try:
                for p in path.iterdir():
                    if p.is_dir():
                        children.append(p)
            except Exception:
                return []
            return sorted(children, key=lambda x: x.name.lower())

        def has_subdirs(self, path: Path) -> bool:
            try:
                for p in path.iterdir():
                    if p.is_dir():
                        return True
            except Exception:
                return False
            return False

        def populate_tree_node(self, node: object) -> None:
            data = getattr(node, "data", None)
            if not isinstance(data, Path):
                return
            node.remove_children()
            children = self.iter_subdirs(data)
            if not children:
                node.allow_expand = False
                return
            node.allow_expand = True
            for child in children:
                child_node = node.add(child.name or str(child), data=child, allow_expand=True)
                if self.has_subdirs(child):
                    self.add_placeholder(child_node)
                else:
                    child_node.allow_expand = False

        def get_root_path(self) -> Path:
            if os.name == "nt":
                anchor = Path(self.current_dir.anchor or Path.cwd().anchor or "C:\\")
                return anchor
            return Path("/")

        def expand_tree_to_path(self, tree: object, target: Path) -> None:
            node = tree.root
            if not isinstance(node.data, Path):
                return
            current = node.data
            target = target.resolve()
            if os.name == "nt":
                if current.drive.lower() != target.drive.lower():
                    return
            else:
                if not str(target).startswith(str(current)):
                    return
            while True:
                if current == target:
                    tree.select_node(node)
                    break
                try:
                    rel = target.relative_to(current)
                except Exception:
                    break
                if not rel.parts:
                    tree.select_node(node)
                    break
                next_part = rel.parts[0]
                self.populate_tree_node(node)
                found = None
                for child in node.children:
                    child_data = getattr(child, "data", None)
                    if isinstance(child_data, Path) and child_data.name == next_part:
                        found = child
                        break
                if not found:
                    tree.select_node(node)
                    break
                node.expand()
                node = found
                current = node.data
            node.expand()

        def prepare_folder_tree(self) -> None:
            tree = self.query_one("#folder_tree", Tree)
            root_path = self.get_root_path()
            root = tree.root
            root.set_label(str(root_path))
            root.data = root_path
            root.allow_expand = True
            root.remove_children()
            self.add_placeholder(root)
            self.populate_tree_node(root)
            root.expand()
            self.expand_tree_to_path(tree, self.current_dir)
            tree.focus()

        def apply_edit(self) -> None:
            new_value = self.query_one("#edit_input", Input).value.strip()
            err_code = validate_target_filename(new_value)
            if err_code:
                self.show_message(
                    self.t("dialog_invalid_targets_title"),
                    self.t("error_row_message", row=self.editing_row + 1, message=self.t(f"error_{err_code}")),
                )
                return
            # Prevent duplicate targets inside current preview set.
            for idx, value in enumerate(self.working_targets):
                if idx != self.editing_row and value.casefold() == new_value.casefold():
                    self.show_message(
                        self.t("dialog_invalid_targets_title"),
                        self.t("error_row_duplicate", row=self.editing_row + 1, name=new_value),
                    )
                    return
            self.working_targets[self.editing_row] = new_value
            self.hide_edit_overlay()
            self.refresh_table()

        def apply_folder_change(self, path: Path) -> None:
            if not path.exists() or not path.is_dir():
                self.show_message(self.t("dialog_invalid_folder_title"), self.t("dialog_invalid_folder_body", path=path))
                return
            new_plans = build_plans_for_directory(path, scan_options)
            self.current_dir = path
            self.plans = new_plans
            self.working_targets = [item.dst for item in self.plans]
            self.hide_folder_overlay()
            self.refresh_table()
            self.refresh_texts()
            if not self.plans:
                self.show_message(self.t("status_no_files"), self.t("dialog_no_files_in_folder", path=path))

        def refresh_texts(self) -> None:
            def with_shortcut(key: str, label: str) -> str:
                return f"{key.upper()} {label}"

            def trim_label(text: str) -> str:
                return text.rstrip(":： ").strip()

            def short(key: str, fallback: str) -> str:
                value = self.t(key).strip()
                return value if value and value != key else fallback

            self.title = self.t("window_title", app_title=app_title)
            self.query_one("#title", Static).update(self.t("window_title", app_title=app_title))
            self.query_one("#path", Static).update(str(self.current_dir))
            self.query_one("#folder", Button).label = with_shortcut("F", short("button_folder_short", self.t("button_choose_folder")))
            self.query_one("#edit_target", Button).label = with_shortcut("E", short("button_edit_short", self.t("button_edit_target")))
            self.query_one("#apply", Button).label = with_shortcut("A", short("button_apply_short", self.t("button_apply")))
            self.query_one("#check_update", Button).label = with_shortcut("U", short("button_update_short", self.t("button_check_update")))
            self.query_one("#about", Button).label = with_shortcut("I", short("button_about_short", self.t("button_about")))
            self.query_one("#help", Button).label = with_shortcut("H", short("button_help_short", self.t("menu_help")))
            self.query_one("#language", Button).label = with_shortcut("L", short("button_language_short", trim_label(self.t("label_language"))))
            self.query_one("#exit", Button).label = with_shortcut("Q", short("button_exit_short", "Exit"))
            self.query_one("#message_ok", Button).label = self.t("dialog_close")
            self.query_one("#edit_save", Button).label = self.t("button_apply")
            self.query_one("#edit_cancel", Button).label = self.t("dialog_close")
            self.query_one("#folder_open", Button).label = self.t("button_choose_folder")
            self.query_one("#folder_cancel", Button).label = self.t("dialog_close")

        def confirm_folder_selection(self) -> None:
            selected = self.folder_candidate or self.current_dir
            self.apply_folder_change(selected.resolve())

        def on_mount(self) -> None:
            self.refresh_table()
            self.refresh_texts()

        def collect_pairs(self) -> tuple[list[tuple[Path, str]], str | None]:
            pairs: list[tuple[Path, str]] = []
            seen: set[str] = set()
            for idx, item in enumerate(self.plans):
                dst_name = self.working_targets[idx].strip()
                err_code = validate_target_filename(dst_name)
                if err_code:
                    return [], self.t("error_row_message", row=idx + 1, message=self.t(f"error_{err_code}"))
                key = dst_name.casefold()
                if key in seen:
                    return [], self.t("error_row_duplicate", row=idx + 1, name=dst_name)
                seen.add(key)
                pairs.append((item.src, dst_name))
            return pairs, None

        def do_apply(self) -> None:
            pairs, err_msg = self.collect_pairs()
            if err_msg:
                self.show_message(self.t("dialog_invalid_targets_title"), err_msg)
                return
            changed, err = apply_rename_pairs(pairs)
            if err:
                self.show_message(self.t("dialog_rename_failed_title"), err)
                return
            self.show_message(self.t("dialog_done_title"), self.t("dialog_done_body", count=changed))
            self.query_one("#apply", Button).disabled = True

        def do_check_update(self) -> None:
            update_status, message = check_update_once(update_url, APP_VERSION)
            message = message.replace("\n", " | ")
            if update_status is None:
                text = self.t("dialog_update_failed", message=message).replace("\n", " | ")
                self.show_message(self.t("dialog_update_title"), text)
            elif update_status:
                text = self.t("dialog_update_available", message=message).replace("\n", " | ")
                self.show_message(self.t("dialog_update_title"), text)
            else:
                text = self.t("dialog_update_uptodate", message=message).replace("\n", " | ")
                self.show_message(self.t("dialog_update_title"), text)

        def do_about(self) -> None:
            about = self.t("about_text", app_title=app_title, version=APP_VERSION, repo_url=GITHUB_URL)
            self.show_message(self.t("dialog_about_title"), about)

        def do_help(self) -> None:
            help_text = self.t("tui_help_text")
            self.show_message(self.t("menu_help"), help_text)

        def do_switch_language(self) -> None:
            self.lang_code = self.next_lang()
            self.cfg["lang"] = self.lang_code
            save_user_config(self.cfg)
            self.refresh_table()
            self.refresh_texts()

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "message_ok":
                self.hide_message()
                return
            if event.button.id == "edit_cancel":
                self.hide_edit_overlay()
                return
            if event.button.id == "folder_cancel":
                self.hide_folder_overlay()
                return
            if event.button.id == "edit_save":
                self.apply_edit()
                return
            if event.button.id == "folder_open":
                self.confirm_folder_selection()
                return
            if event.button.id == "exit":
                self.exit()
                return
            if event.button.id == "folder":
                self.show_folder_overlay()
                return
            if event.button.id == "edit_target":
                self.show_edit_overlay()
                return
            if event.button.id == "apply":
                self.do_apply()
                return
            if event.button.id == "check_update":
                self.do_check_update()
                return
            if event.button.id == "about":
                self.do_about()
                return
            if event.button.id == "help":
                self.do_help()
                return
            if event.button.id == "language":
                self.do_switch_language()

        def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
            if self.query_one("#folder_overlay", Container).has_class("hidden"):
                return
            self.populate_tree_node(event.node)

        def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
            if self.query_one("#folder_overlay", Container).has_class("hidden"):
                return
            data = getattr(event.node, "data", None)
            if isinstance(data, Path):
                self.folder_candidate = data.resolve()
                self.query_one("#folder_label", Static).update(
                    f"{self.t('dialog_folder_select_title')}\n{self.folder_candidate}"
                )

        def on_key(self, event) -> None:
            if event.key != "enter":
                return
            if self.query_one("#folder_overlay", Container).has_class("hidden"):
                return
            self.confirm_folder_selection()
            event.stop()

        def action_edit_target(self) -> None:
            self.show_edit_overlay()

        def action_choose_folder(self) -> None:
            self.show_folder_overlay()

        def action_apply_rename(self) -> None:
            self.do_apply()

        def action_check_update(self) -> None:
            self.do_check_update()

        def action_switch_language(self) -> None:
            self.do_switch_language()

        def action_show_about(self) -> None:
            self.do_about()

        def action_show_help(self) -> None:
            self.do_help()

    app = PreviewApp()
    if run_app:
        app.run()
        return None
    return app


def run_qt_gui_workflow(
    initial_dir: Path,
    backend: str,
    app_title: str,
    app_icon: str | None,
    update_url: str,
    scan_options: ScanOptions,
) -> int:
    if backend == "PySide6":
        from PySide6.QtCore import Qt, QUrl
        from PySide6.QtGui import QDesktopServices, QIcon
        from PySide6.QtWidgets import (
            QApplication,
            QComboBox,
            QFileDialog,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QMainWindow,
            QMessageBox,
            QPushButton,
            QTableWidget,
            QTableWidgetItem,
            QVBoxLayout,
            QWidget,
        )
    else:
        from PyQt6.QtCore import Qt, QUrl
        from PyQt6.QtGui import QDesktopServices, QIcon
        from PyQt6.QtWidgets import (
            QApplication,
            QComboBox,
            QFileDialog,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QMainWindow,
            QMessageBox,
            QPushButton,
            QTableWidget,
            QTableWidgetItem,
            QVBoxLayout,
            QWidget,
        )

    all_packs = load_language_packs()
    fallback_pack = all_packs.get(DEFAULT_LANG, {})

    class MainWindow(QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            if app_icon and Path(app_icon).exists():
                self.setWindowIcon(QIcon(str(Path(app_icon).resolve())))

            self.current_dir: Path | None = None
            self.row_sources: list[Path] = []
            self.repo_url = GITHUB_URL
            self.update_url = update_url
            self.scan_options = scan_options
            self.packs = all_packs
            self.cfg = load_user_config()
            self.lang_code = self.resolve_initial_lang()
            self.folder_label: QLabel | None = None

            container = QWidget()
            root = QVBoxLayout(container)
            self.setCentralWidget(container)

            help_menu = self.menuBar().addMenu("Help")
            self.help_menu = help_menu
            self.about_action = help_menu.addAction("About")
            self.about_action.triggered.connect(self.show_about)
            self.check_update_action = help_menu.addAction("Check Update")
            self.check_update_action.triggered.connect(self.check_update)

            top = QHBoxLayout()
            self.folder_label = QLabel("Folder:")
            top.addWidget(self.folder_label)
            self.dir_input = QLineEdit()
            self.dir_input.setReadOnly(True)
            top.addWidget(self.dir_input, 1)
            self.lang_label = QLabel("Language:")
            top.addWidget(self.lang_label)
            self.lang_combo = QComboBox()
            for code, display in LANG_DISPLAY_NAMES.items():
                self.lang_combo.addItem(display, code)
            top.addWidget(self.lang_combo)
            self.choose_btn = QPushButton("Choose Folder")
            self.rescan_btn = QPushButton("Rescan")
            top.addWidget(self.choose_btn)
            top.addWidget(self.rescan_btn)
            root.addLayout(top)

            self.table = QTableWidget(0, 5)
            self.table.setHorizontalHeaderLabels(["Current", "Target (Editable)", "Title Len", "Name Len", "Note"])
            self.table.horizontalHeader().setStretchLastSection(True)
            root.addWidget(self.table, 1)

            bottom = QHBoxLayout()
            self.status = QLabel("Choose a folder to begin.")
            bottom.addWidget(self.status, 1)
            self.about_btn = QPushButton("About")
            bottom.addWidget(self.about_btn)
            self.check_update_btn = QPushButton("Check Update")
            bottom.addWidget(self.check_update_btn)
            self.apply_btn = QPushButton("Apply Rename")
            bottom.addWidget(self.apply_btn)
            root.addLayout(bottom)

            self.choose_btn.clicked.connect(self.choose_folder)
            self.rescan_btn.clicked.connect(self.rescan)
            self.apply_btn.clicked.connect(self.apply_rename)
            self.about_btn.clicked.connect(self.show_about)
            self.check_update_btn.clicked.connect(self.check_update)
            self.table.itemChanged.connect(self.on_item_changed)
            self.lang_combo.currentIndexChanged.connect(self.on_language_changed)

            self.set_lang_combo(self.lang_code)
            self.apply_language(self.lang_code)

            self.resize(1400, 860)

        def t(self, key: str, **kwargs: object) -> str:
            pack = self.packs.get(self.lang_code, {})
            template = pack.get(key) or fallback_pack.get(key) or key
            try:
                return template.format(**kwargs)
            except Exception:
                return template

        def resolve_initial_lang(self) -> str:
            user_lang = normalize_lang_code(str(self.cfg.get("lang", "")))
            if user_lang in self.packs:
                return user_lang
            sys_lang = detect_system_language()
            if sys_lang in self.packs:
                return sys_lang
            return DEFAULT_LANG

        def set_lang_combo(self, lang_code: str) -> None:
            idx = self.lang_combo.findData(lang_code)
            if idx >= 0:
                self.lang_combo.blockSignals(True)
                self.lang_combo.setCurrentIndex(idx)
                self.lang_combo.blockSignals(False)

        def note_text(self, code: str) -> str:
            if code == "same":
                return self.t("note_same")
            return code

        def apply_language(self, lang_code: str) -> None:
            self.lang_code = lang_code
            self.setWindowTitle(self.t("window_title", app_title=app_title))
            self.help_menu.setTitle(self.t("menu_help"))
            self.about_action.setText(self.t("menu_about"))
            self.check_update_action.setText(self.t("menu_check_update"))
            if self.folder_label:
                self.folder_label.setText(self.t("label_folder"))
            self.lang_label.setText(self.t("label_language"))
            self.choose_btn.setText(self.t("button_choose_folder"))
            self.rescan_btn.setText(self.t("button_rescan"))
            self.about_btn.setText(self.t("button_about"))
            self.check_update_btn.setText(self.t("button_check_update"))
            self.apply_btn.setText(self.t("button_apply"))
            self.table.setHorizontalHeaderLabels(
                [
                    self.t("table_current"),
                    self.t("table_target"),
                    self.t("table_title_len"),
                    self.t("table_name_len"),
                    self.t("table_note"),
                ]
            )
            if not self.current_dir:
                self.status.setText(self.t("status_choose_folder"))
            self.refresh_table_notes_and_lengths()

        def refresh_table_notes_and_lengths(self) -> None:
            if self.table.rowCount() == 0:
                return
            self.table.blockSignals(True)
            for row in range(self.table.rowCount()):
                dst_item = self.table.item(row, 1)
                if not dst_item:
                    continue
                dst = (dst_item.text() or "").strip()
                self.table.item(row, 2).setText(str(calc_title_len_from_target(dst)))
                self.table.item(row, 3).setText(str(len(dst)))
                same = self.row_sources[row].name == dst if row < len(self.row_sources) else False
                note_code = calc_length_note(len(dst), same)
                self.table.item(row, 4).setText(self.note_text(note_code))
            self.table.blockSignals(False)

        def choose_folder(self) -> None:
            start = str(self.current_dir or initial_dir)
            selected = QFileDialog.getExistingDirectory(self, self.t("dialog_folder_select_title"), start)
            if not selected:
                return
            self.load_directory(Path(selected))

        def show_about(self) -> None:
            msg = QMessageBox(self)
            msg.setWindowTitle(self.t("dialog_about_title"))
            msg.setIcon(QMessageBox.Icon.Information)
            msg.setText(
                self.t(
                    "about_text",
                    app_title=app_title,
                    version=APP_VERSION,
                    repo_url=self.repo_url,
                )
            )
            open_btn = msg.addButton(self.t("about_open_github"), QMessageBox.ButtonRole.ActionRole)
            msg.addButton(self.t("dialog_close"), QMessageBox.ButtonRole.RejectRole)
            msg.exec()
            if msg.clickedButton() == open_btn:
                QDesktopServices.openUrl(QUrl(self.repo_url))

        def check_update(self) -> None:
            status, message = check_update_once(self.update_url, APP_VERSION)
            if status is None:
                QMessageBox.warning(self, self.t("dialog_update_title"), self.t("dialog_update_failed", message=message))
                return
            if status:
                QMessageBox.information(self, self.t("dialog_update_title"), self.t("dialog_update_available", message=message))
            else:
                QMessageBox.information(self, self.t("dialog_update_title"), self.t("dialog_update_uptodate", message=message))

        def rescan(self) -> None:
            if self.current_dir is None:
                self.choose_folder()
                return
            self.load_directory(self.current_dir)

        def load_directory(self, folder: Path) -> None:
            if not folder.exists() or not folder.is_dir():
                QMessageBox.warning(self, self.t("dialog_invalid_folder_title"), self.t("dialog_invalid_folder_body", path=folder))
                return
            self.current_dir = folder
            self.dir_input.setText(str(folder))

            plans = build_plans_for_directory(folder, self.scan_options)
            if not plans:
                self.table.blockSignals(True)
                self.table.setRowCount(0)
                self.table.blockSignals(False)
                self.row_sources = []
                self.status.setText(self.t("status_no_files"))
                return

            self.table.blockSignals(True)
            self.table.setRowCount(len(plans))
            self.row_sources = []
            for row, plan in enumerate(plans):
                self.row_sources.append(plan.src)

                src_item = QTableWidgetItem(plan.src.name)
                src_item.setFlags(src_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, 0, src_item)

                dst_item = QTableWidgetItem(plan.dst)
                dst_item.setFlags(dst_item.flags() | Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, 1, dst_item)

                self.table.setItem(row, 2, QTableWidgetItem(str(calc_title_len_from_target(plan.dst))))
                self.table.setItem(row, 3, QTableWidgetItem(str(len(plan.dst))))
                note_code = calc_length_note(len(plan.dst), plan.src.name == plan.dst)
                self.table.setItem(row, 4, QTableWidgetItem(self.note_text(note_code)))
            self.table.blockSignals(False)
            self.table.resizeColumnsToContents()
            self.status.setText(self.t("status_loaded", count=len(plans)))

        def on_item_changed(self, item: QTableWidgetItem) -> None:
            if item.column() != 1:
                return
            row = item.row()
            dst = (item.text() or "").strip()
            self.table.blockSignals(True)
            self.table.item(row, 2).setText(str(calc_title_len_from_target(dst)))
            self.table.item(row, 3).setText(str(len(dst)))
            same = self.row_sources[row].name == dst if row < len(self.row_sources) else False
            note_code = calc_length_note(len(dst), same)
            self.table.item(row, 4).setText(self.note_text(note_code))
            self.table.blockSignals(False)

        def on_language_changed(self, _: int) -> None:
            code = str(self.lang_combo.currentData() or DEFAULT_LANG)
            if code not in self.packs:
                code = DEFAULT_LANG
            self.cfg["lang"] = code
            save_user_config(self.cfg)
            self.apply_language(code)

        def collect_pairs(self) -> tuple[list[tuple[Path, str]], str | None]:
            if self.current_dir is None:
                return [], self.t("error_no_folder_selected")
            pairs: list[tuple[Path, str]] = []
            seen: set[str] = set()
            for row, src in enumerate(self.row_sources):
                dst_item = self.table.item(row, 1)
                dst_name = (dst_item.text() if dst_item else "").strip()
                err_code = validate_target_filename(dst_name)
                if err_code:
                    return [], self.t("error_row_message", row=row + 1, message=self.t(f"error_{err_code}"))
                key = dst_name.casefold()
                if key in seen:
                    return [], self.t("error_row_duplicate", row=row + 1, name=dst_name)
                seen.add(key)
                pairs.append((src, dst_name))
            return pairs, None

        def apply_rename(self) -> None:
            pairs, err = self.collect_pairs()
            if err:
                QMessageBox.warning(self, self.t("dialog_invalid_targets_title"), err)
                return
            if not pairs:
                QMessageBox.information(self, self.t("dialog_noop_title"), self.t("dialog_noop_body"))
                return

            changed, apply_err = apply_rename_pairs(pairs)
            if apply_err:
                QMessageBox.critical(self, self.t("dialog_rename_failed_title"), apply_err)
                return
            QMessageBox.information(self, self.t("dialog_done_title"), self.t("dialog_done_body", count=changed))
            if self.current_dir:
                self.load_directory(self.current_dir)

    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    win.status.setText(win.t("status_choose_folder"))

    app.exec()
    return 0


def render_tk_gui_preview(plans: list[RenamePlan]) -> None:
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("Rename Preview")
    root.geometry("1280x720")

    columns = ("current", "target", "title_len", "name_len", "note")
    tree = ttk.Treeview(root, columns=columns, show="headings")
    for c, t, w in [
        ("current", "Current", 420),
        ("target", "Target", 500),
        ("title_len", "Title Len", 90),
        ("name_len", "Name Len", 90),
        ("note", "Note", 120),
    ]:
        tree.heading(c, text=t)
        tree.column(c, width=w, anchor="w")

    scrollbar_y = ttk.Scrollbar(root, orient="vertical", command=tree.yview)
    scrollbar_x = ttk.Scrollbar(root, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=scrollbar_y.set, xscrollcommand=scrollbar_x.set)

    for item in plans:
        try:
            dst_len = int(item.reason["name_len"])
        except ValueError:
            dst_len = len(item.dst)
        note = ""
        if dst_len > WINDOWS_FILENAME_LIMIT:
            note = ">255"
        elif dst_len > SAFE_FILENAME_LIMIT:
            note = ">200"
        elif item.src.name == item.dst:
            note = "same"
        tree.insert("", "end", values=(item.src.name, item.dst, item.reason["title_len"], item.reason["name_len"], note))

    tree.pack(fill="both", expand=True, side="top")
    scrollbar_y.pack(fill="y", side="right")
    scrollbar_x.pack(fill="x", side="bottom")
    root.mainloop()


def render_preview(
    plans: list[RenamePlan],
    ui_mode: str,
    app_title: str,
    update_url: str,
    target_dir: Path,
    scan_options: ScanOptions,
) -> None:
    if ui_mode == "gui":
        print("[WARN] GUI mode is handled before preview rendering. Fallback to TUI/CLI preview here.")
        ui_mode = "tui"

    if ui_mode == "tui":
        tui_backend = detect_tui_backend(try_install=True)
        if tui_backend == "textual":
            render_textual_tui_preview(plans, app_title, update_url, target_dir, scan_options)
            return
        if tui_backend == "rich":
            render_rich_tui_preview(plans)
            return
        print("[WARN] No TUI package available. Fallback to CLI preview.")
        render_cli_preview(plans)
        return

    if ui_mode == "auto":
        # GUI auto workflow is handled in main() with Qt.
        if detect_tui_backend(try_install=False):
            render_preview(plans, "tui", app_title, update_url, target_dir, scan_options)
            return
        render_cli_preview(plans)
        return

    render_cli_preview(plans)


def main() -> int:
    parser = argparse.ArgumentParser(description="Preview/apply ebook renaming by metadata")
    parser.add_argument("--dir", default=".", help="Target directory (default: current dir)")
    parser.add_argument("--apply", action="store_true", help="Apply rename. Default is preview only")
    parser.add_argument("--gui", action="store_true", help="Preview with best available GUI backend")
    parser.add_argument("--tui", action="store_true", help="Preview with best available TUI backend")
    parser.add_argument("--app-title", default="Ebook Renamer", help="GUI app window title")
    parser.add_argument("--app-icon", default="", help="GUI app icon path (.ico/.icns/.png)")
    parser.add_argument("--check-update", action="store_true", help="Check latest version from release metadata and exit")
    parser.add_argument("--update-url", default=UPDATE_METADATA_URL, help="Update metadata URL (latest.json)")
    parser.add_argument("--allow-ocr", action="store_true", help="Reserved flag: OCR fallback (not implemented yet)")
    parser.add_argument("--allow-online", action="store_true", help="Reserved flag: online metadata lookup (not implemented yet)")
    parser.add_argument(
        "--ui",
        choices=["auto", "cli", "gui", "tui"],
        default="auto",
        help="Preview UI mode (default: auto)",
    )
    args = parser.parse_args()

    if args.gui and args.tui:
        print("[ERROR] --gui and --tui cannot be used together.")
        return 1

    if args.allow_ocr:
        print("[INFO] --allow-ocr is enabled, but OCR fallback is not implemented yet.")
    if args.allow_online:
        print("[INFO] --allow-online is enabled, but online metadata lookup is not implemented yet.")

    if args.check_update:
        status, message = check_update_once(args.update_url, APP_VERSION)
        prefix = "[UPDATE]"
        print(f"{prefix} {message}")
        return 0 if status is not None else 1

    scan_options = ScanOptions(allow_ocr=args.allow_ocr, allow_online=args.allow_online)

    if args.gui:
        args.ui = "gui"
    elif args.tui:
        args.ui = "tui"

    if args.ui in {"gui", "auto"}:
        if can_show_gui():
            gui_backend = detect_gui_backend(try_install=(args.ui == "gui"))
            if gui_backend in {"PySide6", "PyQt6"}:
                if args.apply:
                    print("[INFO] --apply is ignored in GUI mode; use the Apply button in the window.")
                start_dir = Path(args.dir).resolve() if Path(args.dir).exists() else Path.home()
                try:
                    return run_qt_gui_workflow(
                        initial_dir=start_dir,
                        backend=gui_backend,
                        app_title=args.app_title,
                        app_icon=args.app_icon or None,
                        update_url=args.update_url,
                        scan_options=scan_options,
                    )
                except Exception as e:
                    print(f"[WARN] Failed to launch GUI workflow: {e}. Falling back to TUI/CLI.")
            if args.ui == "gui":
                print("[WARN] No Qt GUI backend available. Falling back to TUI/CLI.")
        elif args.ui == "gui":
            print("[WARN] GUI environment not detected. Falling back to TUI/CLI.")

    target = Path(args.dir).resolve()
    if not target.exists() or not target.is_dir():
        print(f"[ERROR] Invalid directory: {target}")
        return 1

    plans = build_plans_for_directory(target, scan_options)
    if not plans:
        print("[INFO] No .epub/.pdf files found.")
        return 0

    render_preview(plans, args.ui, args.app_title, args.update_url, target, scan_options)

    if not args.apply:
        print("\n[INFO] Preview only. Use --apply to rename files.")
        return 0

    pairs = [(item.src, item.dst) for item in plans]
    changed, err = apply_rename_pairs(pairs)
    if err:
        print(f"[ERROR] Rename failed: {err}")
        return 1
    print(f"\n[INFO] Rename complete. Changed {changed} file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
