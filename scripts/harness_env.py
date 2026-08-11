"""Load local harness secrets from a repo-root ``.env`` file (stdlib only)."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_PATH = REPO_ROOT / ".env"
DEFAULT_UPSTREAM_REPO_ROOT = Path(r"E:\PodcastRoom\Cursor\automated-video-editing")


def upstream_env_path() -> Path:
    raw = os.environ.get("PIAB_UPSTREAM_ROOT", "").strip()
    root = Path(raw) if raw else DEFAULT_UPSTREAM_REPO_ROOT
    return root.resolve() / ".env"


def _apply_dotenv_file(env_path: Path, *, override: bool) -> None:
    values = parse_dotenv(env_path.read_text(encoding="utf-8"))
    for key, value in values.items():
        if override or key not in os.environ:
            os.environ[key] = value


def parse_dotenv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def load_harness_env(
    path: Path | None = None,
    *,
    override: bool = False,
) -> Path | None:
    """
    Load repo ``.env`` (and upstream fallback ``.env``) into ``os.environ``.

    Local repo ``.env`` is loaded first; upstream values fill gaps only unless
    ``override`` is True for the primary file.

    Returns the primary path loaded, or the upstream path, or ``None``.
    """
    loaded: Path | None = None
    env_path = (path or DEFAULT_ENV_PATH).resolve()
    if env_path.is_file():
        _apply_dotenv_file(env_path, override=override)
        loaded = env_path

    upstream_path = upstream_env_path()
    if upstream_path.is_file() and upstream_path != env_path:
        _apply_dotenv_file(upstream_path, override=False)

    return loaded or (upstream_path if upstream_path.is_file() else None)


def merge_env_file(path: Path, updates: dict[str, str]) -> None:
    """Create or update keys in a dotenv file, preserving other lines and comments."""
    path = path.resolve()
    existing_lines: list[str] = []
    seen: set[str] = set()
    if path.is_file():
        existing_lines = path.read_text(encoding="utf-8").splitlines()
    out_lines: list[str] = []
    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out_lines.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in updates:
            out_lines.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out_lines.append(line)
    for key, value in updates.items():
        if key not in seen:
            out_lines.append(f"{key}={value}")
    path.write_text("\n".join(out_lines).rstrip() + "\n", encoding="utf-8")
