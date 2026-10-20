"""Nothing private or secret may be tracked by Git (checked against `git ls-files`)."""
from pathlib import Path
import re
import subprocess
import pytest

ROOT = Path(__file__).resolve().parent.parent


def tracked() -> list[str]:
    try:
        out = subprocess.run(["git", "-c", "safe.directory=*", "ls-files"], cwd=ROOT, capture_output=True,
                             text=True, check=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git is not available")
    return [line for line in out.stdout.splitlines() if line]


SECRET_PATTERNS = {
    "OpenAI-style key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "sk-ant style key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "key assignment with a value": re.compile(r"(?i)\b(api[_-]?key|secret|token|password)\s*[=:]\s*['\"]?[A-Za-z0-9/+_-]{16,}"),
}
# Content that only exists in the private dataset.
DATASET_CONTENT = re.compile(r"ANONVIN\d|BLCODE\d{6}|^Email-ID: EMAIL-", re.MULTILINE)


def test_no_private_or_generated_files_are_tracked():
    files = tracked()
    forbidden = [f for f in files if f == ".env" or f.startswith(".venv/") or f.startswith("data/") and f != "data/private/.gitkeep"
                 or re.search(r"\.(sqlite3?|db|zip|pdf|log)(-\w+)?$", f) or "/__pycache__/" in f]
    assert forbidden == []


def test_no_secrets_in_tracked_files():
    hits = []
    for name in tracked():
        path = ROOT / name
        if not path.is_file() or path.stat().st_size > 2_000_000:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        hits += [f"{name}: {label}" for label, pattern in SECRET_PATTERNS.items() if pattern.search(text)]
    assert hits == []


def test_no_dataset_content_in_tracked_files():
    hits = [name for name in tracked() if (ROOT / name).is_file() and name.endswith((".py", ".md", ".json", ".txt", ".toml"))
            and DATASET_CONTENT.search((ROOT / name).read_text(encoding="utf-8", errors="ignore"))
            and name != "tests/test_repo_hygiene.py"]
    assert hits == []


def test_env_example_holds_names_and_local_defaults_only():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assignments = dict(line.split("=", 1) for line in text.splitlines() if "=" in line and not line.lstrip().startswith("#"))
    assert not any(re.search(r"(?i)key|secret|token|password", name) and value.strip() for name, value in assignments.items())


def test_gitignore_covers_the_sensitive_paths():
    rules = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for needle in (".env", ".venv/", "data/private/*", "*.sqlite*", "*.db", "__pycache__/"):
        assert needle in rules, needle


def test_the_detectors_do_catch_what_they_are_meant_to():
    assert SECRET_PATTERNS["OpenAI-style key"].search("OPENAI=sk-" + "a" * 24)
    assert SECRET_PATTERNS["GitHub token"].search("ghp_" + "A1" * 18)
    assert SECRET_PATTERNS["key assignment with a value"].search('API_KEY="' + "x1" * 10 + '"')
    assert not SECRET_PATTERNS["key assignment with a value"].search("API_KEY=")
    assert DATASET_CONTENT.search("VIN: ANONVIN" + "0" * 10) and DATASET_CONTENT.search("Email-ID: EMAIL-001")
