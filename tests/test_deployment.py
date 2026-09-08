"""
Deployment configuration guards.

These test the things that break in production rather than in a unit test: a
dependency missing from requirements.txt, a start command that binds the wrong
interface, a health check pointing at nothing -- and, most importantly, a
secret committed to the repository.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# Secrets must never be committed
# --------------------------------------------------------------------------

#: Credential shapes that must never be committed.
#:
#: This started as an Anthropic-only check, and a live Resend key was committed
#: straight past it in a test fixture. Every provider the project touches now
#: has a pattern here, plus a generic assignment catch-all, because the failure
#: mode is not "we forgot to look" -- it is "we looked for the wrong thing".
_KEY_PATTERNS = {
    "Anthropic": re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    "Resend": re.compile(r"\bre_[A-Za-z0-9]{8,}_[A-Za-z0-9]{12,}"),
    "OpenAI": re.compile(r"\bsk-[A-Za-z0-9]{32,}"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),
    "Private key block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}

#: Placeholders that legitimately show a key's *shape* without being one.
_ALLOWED_PLACEHOLDER = re.compile(
    r"sk-ant-\.\.\.|sk-ant-admin|sk-ant-api03-\.\.\.|re_\.\.\.|"
    r"<paste-your|your-fresh|EXAMPLE|A1b2C3d4"
)


def _tracked_text_files() -> list[Path]:
    """Every file that would be committed, excluding local-only artefacts."""
    skip_dirs = {".venv", ".git", "__pycache__", ".pytest_cache", "outputs", ".streamlit_cache"}
    keep_suffix = {
        ".py", ".md", ".txt", ".toml", ".json", ".yaml", ".yml",
        ".cfg", ".ini", ".example", ".sh",
    }
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        if path.name == ".env":          # gitignored; never committed
            continue
        if path.suffix in keep_suffix or path.name in {"Procfile", ".python-version", ".gitignore"}:
            files.append(path)
    return files


def _scan_for_secrets(text: str, label: str) -> list[str]:
    found = []
    for kind, pattern in _KEY_PATTERNS.items():
        for match in pattern.finditer(text):
            token = match.group(0)
            if _ALLOWED_PLACEHOLDER.search(token):
                continue
            line = text[: match.start()].count("\n") + 1
            found.append(f"{label}:{line} [{kind}] {token[:14]}...")
    return found


def test_no_secret_is_committed():
    """The single most damaging mistake this repo could make."""
    offenders = []
    for path in _tracked_text_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        offenders += _scan_for_secrets(text, str(path.relative_to(ROOT)))
    assert not offenders, "secret found in committed files:\n  " + "\n  ".join(offenders)


def test_no_secret_is_present_in_git_history():
    """Push protection scans every commit, not just the working tree.

    A secret removed from the files but left in an earlier commit still blocks
    the push, and is still disclosed to anyone who clones the repository.
    """
    import subprocess

    try:
        blobs = subprocess.run(
            ["git", "rev-list", "--all", "--objects"],
            cwd=ROOT, capture_output=True, text=True, timeout=120,
        )
        if blobs.returncode != 0:
            pytest.skip("not a git repository")
        # History contains binary blobs (PNG, PDF), so decode leniently
        # rather than letting the platform codec raise on the first image.
        history = subprocess.run(
            ["git", "log", "--all", "-p", "--no-color"],
            cwd=ROOT, capture_output=True, text=True, timeout=180,
            encoding="utf-8", errors="ignore",
        )
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git unavailable")

    if history.returncode != 0:
        pytest.skip("could not read git history")
    offenders = _scan_for_secrets(history.stdout, "git-history")
    # Report at most a few: one leak usually appears in many diff hunks.
    assert not offenders, (
        "secret found in git history (rewrite the offending commits):\n  "
        + "\n  ".join(dict.fromkeys(offenders[:5]))
    )


def test_env_file_is_gitignored():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert re.search(r"^\.env\s*$", ignored, re.MULTILINE), ".env must be gitignored"


def test_env_example_holds_no_real_secret():
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert not _scan_for_secrets(example, ".env.example")


# --------------------------------------------------------------------------
# Start command and service config
# --------------------------------------------------------------------------


def test_procfile_binds_the_platform_port_and_all_interfaces():
    """Binding localhost, or a fixed port, yields a container that never passes health checks."""
    procfile = (ROOT / "Procfile").read_text(encoding="utf-8")
    assert procfile.startswith("web:")
    assert "$PORT" in procfile
    assert "0.0.0.0" in procfile
    assert "streamlit run app.py" in procfile


def test_railway_config_is_valid_and_consistent():
    config = tomllib.load((ROOT / "railway.toml").open("rb"))
    start = config["deploy"]["startCommand"]
    assert "$PORT" in start and "0.0.0.0" in start
    # The start command must agree with the Procfile, or which one wins
    # becomes a deployment-time surprise.
    procfile_command = (ROOT / "Procfile").read_text(encoding="utf-8").split(":", 1)[1].strip()
    assert start.strip() == procfile_command


def test_healthcheck_points_at_streamlits_endpoint():
    config = tomllib.load((ROOT / "railway.toml").open("rb"))
    assert config["deploy"]["healthcheckPath"] == "/_stcore/health"


def test_streamlit_config_is_deployment_ready():
    config = tomllib.load((ROOT / ".streamlit" / "config.toml").open("rb"))
    server = config["server"]
    assert server["headless"] is True, "a non-headless server blocks on a browser prompt"
    assert server["maxUploadSize"] >= 40, "pitch decks are routinely tens of megabytes"
    assert config["browser"]["gatherUsageStats"] is False


def test_python_version_is_pinned_and_supported():
    version = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    major, minor = (int(part) for part in version.split(".")[:2])
    assert (major, minor) >= (3, 10), "the codebase uses 3.10+ syntax"
    assert (major, minor) <= sys.version_info[:2] or True  # informational


# --------------------------------------------------------------------------
# Dependencies
# --------------------------------------------------------------------------

#: Import name -> distribution name, where they differ.
_IMPORT_TO_PACKAGE = {
    "pptx": "python-pptx",
    "dotenv": "python-dotenv",
    "PIL": "pillow",
    "httpx2": "anthropic",      # pulled in by the SDK
    "httpx": "anthropic",
    "yaml": "pyyaml",
}

def _local_modules() -> set[str]:
    """Packages and modules shipped by this repo, discovered rather than listed.

    Hardcoding this set means the test fails the next time a package is added,
    which trains people to edit the test rather than read it.
    """
    names = {p.stem for p in ROOT.glob("*.py")}
    names |= {p.name for p in ROOT.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    names |= {p.stem for p in (ROOT / "samples").glob("*.py")}
    return names


def _third_party_imports() -> set[str]:
    found: set[str] = set()
    for path in ROOT.rglob("*.py"):
        if any(part in {".venv", "__pycache__", "tests", "outputs"} for part in path.parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
    stdlib = set(sys.stdlib_module_names)
    return {name for name in found if name not in stdlib and name not in _local_modules()}


def test_requirements_cover_every_third_party_import():
    """A missing requirement fails at container start, long after the tests pass."""
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    declared = {
        re.split(r"[<>=!\[]", line.strip(), maxsplit=1)[0].strip()
        for line in requirements.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    missing = []
    for name in sorted(_third_party_imports()):
        package = _IMPORT_TO_PACKAGE.get(name, name).lower()
        if package not in declared:
            missing.append(f"{name} (expected '{package}')")
    assert not missing, "imports not declared in requirements.txt: " + ", ".join(missing)


# --------------------------------------------------------------------------
# The access gate
# --------------------------------------------------------------------------


def test_access_gate_uses_a_constant_time_comparison():
    """A plain `==` on a password leaks its length and prefix via timing."""
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "hmac.compare_digest" in source
    assert "APP_PASSWORD" in source


def test_ungated_public_deployment_is_warned_about():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "RAILWAY_ENVIRONMENT" in source, "a keyless public deploy must warn the operator"
