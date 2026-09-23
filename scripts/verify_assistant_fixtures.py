"""Verify upload validation and parser boundaries, without an LLM or antivirus bypass.

Run: backend/.venv/Scripts/python.exe scripts/verify_assistant_fixtures.py
This is a parser preflight, not an end-to-end upload or model-quality test.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.errors import AppError
from app.modules.documents.service import MIME, _validate_file

CASES = [
    ("01_small_spec.txt", "ready", ["CHECK-START-ALFA", "CHECK-END-OMEGA", "TEST-NOT-IN-CATALOG"], []),
    ("02_large_text.txt", "partial", ["CHECK-START-ALFA"], ["CHECK-FAR-END-LARGE-TEXT"]),
    ("03_spec_500_rows.csv", "partial", ["CSV-ROW-0199"], ["CSV-ROW-0200", "CSV-ROW-0500"]),
    ("04_spec_1200_rows.xlsx", "partial", ["SHEET-1-ROW-199"], ["SHEET-1-ROW-200", "SHEET-3-ROW-400"]),
    ("05_spec_22_sheets.xlsx", "partial", ["SHEET-20-ROW-4"], ["SHEET-21-ROW-1", "SHEET-22-ROW-4"]),
    ("06_specification.docx", "ready", ["DOCX-START-ALFA", "DOCX-END-OMEGA", "990100003_"], []),
    ("07_catalog_30_pages.pdf", "partial", ["PDF-PAGE-20-CHECK"], ["PDF-PAGE-21-CHECK", "PDF-LAST-OMEGA"]),
    ("08_scanned_spec.pdf", "partial", [], ["SCAN-PAGE-1"]),
    ("09_photo_spec.jpg", "ready", [], ["PHOTO-CHECK-GAMMA"]),
    ("10_over_10MiB.png", "rejected", [], []),
]


def main():
    source = ROOT / "test-files" / "assistant"
    qa = ROOT / "tmp" / "fixture-qa" / "parser"
    qa.mkdir(parents=True, exist_ok=True)
    assert {p.name for p in source.iterdir()} == {c[0] for c in CASES}, "Expected exactly ten fixtures"
    report = {"scope": "Local MIME/size/OOXML validation and isolated parser; no antivirus or LLM calls", "files": []}
    for name, expected_status, present, absent in CASES:
        path = source / name
        content = path.read_bytes()
        entry = {"file": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        try:
            extension, _ = _validate_file(name, MIME[path.suffix], content)
        except AppError as error:
            assert expected_status == "rejected" and error.code == "file_size" and error.status == 413
            entry.update(status="rejected", error=error.code, http_status=error.status)
        else:
            image_out = qa / (path.stem + ".image")
            run = subprocess.run(
                [sys.executable, "-I", str(ROOT / "backend/app/modules/documents/parser.py"), str(path), extension, str(image_out)],
                capture_output=True, encoding="utf-8", timeout=30, check=True,
            )
            parsed = json.loads(run.stdout)
            assert parsed["status"] == expected_status, (name, parsed["status"])
            for marker in present:
                assert marker in parsed["text"], (name, "missing", marker)
            for marker in absent:
                assert marker not in parsed["text"], (name, "unexpected", marker)
            if name.startswith("02_"):
                assert len(parsed["text"]) == 40000
            if name.startswith("08_"):
                assert not parsed["text"].strip()
                assert all(image_out.with_name(image_out.name + f"-{i}").is_file() for i in range(2))
            if name.startswith("09_"):
                from PIL import Image
                with Image.open(image_out) as image:
                    assert max(image.size) == 2000
            entry.update(status=parsed["status"], text_characters=len(parsed["text"]),
                         warnings=parsed["warnings"], image_media_type=parsed["image_media_type"],
                         markers_present=present, markers_absent=absent)
        entry["passed"] = True
        report["files"].append(entry)
        print(f"PASS {name}: {entry['status']}")
    report["passed"] = len(CASES)
    (ROOT / "test-files/verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
