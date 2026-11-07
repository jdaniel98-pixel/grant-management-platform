"""Keep the legacy Grant Assistant folder (real data) out of Git, uploads and the application."""
import offline_guard  # noqa: F401  (keeps this suite offline; must stay the first import)
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "src" / "grant_platform"
LEGACY = "Grant Assistant/"


def ignore_lines(name):
    path = ROOT / name
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


class LegacyFolderSafeguards(unittest.TestCase):
    def test_excluded_from_git_and_railway_uploads(self):
        self.assertIn(LEGACY, ignore_lines(".gitignore"))
        self.assertIn(LEGACY, ignore_lines(".railwayignore"))

    def test_future_packaging_files_exclude_it(self):
        for name in (".dockerignore",):
            if (ROOT / name).exists():
                self.assertIn(LEGACY, ignore_lines(name), name)

    def test_platform_code_never_references_legacy_code_or_data(self):
        pattern = re.compile(r"grant[ _]assistant|legacy[ _]org", re.IGNORECASE)
        for path in PLATFORM.rglob("*.py"):
            self.assertIsNone(pattern.search(path.read_text(encoding="utf-8")), path.name)

    def test_tests_and_fixtures_never_reference_legacy_data(self):
        pattern = re.compile(r"legacy_org_processor|Grant Assistant/legacy_org", re.IGNORECASE)
        for path in (ROOT / "tests").rglob("*"):
            if path.suffix in {".py", ".json"} and path.name != Path(__file__).name:
                self.assertIsNone(pattern.search(path.read_text(encoding="utf-8")), path.name)


if __name__ == "__main__":
    unittest.main()
