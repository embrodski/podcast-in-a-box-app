"""Shared podcast phrase gates (start/end/pause) for PIAB and harness autocut."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from harness_episode_lib import REPO_ROOT

PHRASE_GATES_FILENAME = "podcast-phrase-gates.json"

# Spoken countdown numbers supported when configuring start_countdown_tokens.
COUNTDOWN_NUMBER_WORDS: tuple[str, ...] = (
    "ten",
    "nine",
    "eight",
    "seven",
    "six",
    "five",
    "four",
    "three",
    "two",
)

EMBEDDED_DEFAULTS: dict[str, Any] = {
    "start_trigger_phrase": "I solemnly swear I'm up to no good",
    "start_countdown_tokens": ["five", "four", "three", "two"],
    "start_countdown_suffix_tokens": ["one", "zero"],
    "start_countdown_allow_in": True,
    "end_phrases": [
        "Be excellent to each other and party on dudes",
        "Hut of brown, now sit down",
    ],
    "start_preroll_sec": 1.0,
    "end_postroll_sec": 1.0,
    "pause_phrase": "Computer Freeze Program.",
    "unpause_phrases": [
        "Computer Resume Program",
        "Computer Unfreeze Program",
    ],
    "abort_phrase": "Emergency override - Eject the warp core",
    "pause_preroll_sec": 0.25,
    "pause_postroll_sec": 0.7,
    "flag_phrases": [
        "Computer Drop Flag",
        "Computer Raise Flag",
        "Computer Timestamp",
        "Computer Drop Timestamp",
    ],
}

_STATE_OVERRIDE_KEYS = (
    "start_trigger_phrase",
    "start_countdown_tokens",
    "start_countdown_suffix_tokens",
    "start_countdown_allow_in",
    # Legacy keys (still accepted on state / file)
    "start_phrase",
    "start_phrase_countdown_tokens",
    "start_phrase_countdown_suffix",
    "end_phrase",
    "end_phrases",
    "start_preroll_sec",
    "end_postroll_sec",
    "pause_phrase",
    "unpause_phrases",
    "unpause_phrase",
    "abort_phrase",
    "pause_preroll_sec",
    "pause_postroll_sec",
    "flag_phrases",
    "flag_phrase",
)


def _tokenize_phrase(phrase: str) -> list[str]:
    cleaned = re.sub(r"[^\w\s']", " ", phrase.lower())
    return [t for t in cleaned.split() if t]


def _split_legacy_combined_start_phrase(
    combined: str,
    countdown_tokens: list[str],
) -> str:
    """Derive trigger text from a legacy combined start_phrase string."""
    tokens = _tokenize_phrase(combined)
    if not tokens or not countdown_tokens:
        return combined.strip()
    countdown_set = {t for t in countdown_tokens}
    first_cd_idx: int | None = None
    for idx, tok in enumerate(tokens):
        if tok in countdown_set:
            first_cd_idx = idx
            break
    if first_cd_idx is None:
        return combined.strip()
    prefix_end = first_cd_idx
    if first_cd_idx > 0 and tokens[first_cd_idx - 1] == "in":
        prefix_end = first_cd_idx - 1
    if prefix_end <= 0:
        return combined.strip()
    return " ".join(tokens[:prefix_end])


def flag_phrases_from_gates(gates: dict[str, Any]) -> list[str]:
    """Return configured flag phrases (primary + alternates), in order."""
    if gates.get("flag_phrases"):
        return [str(p) for p in gates["flag_phrases"] if str(p).strip()]
    if gates.get("flag_phrase"):
        return [str(gates["flag_phrase"])]
    return []


def end_phrases_from_gates(gates: dict[str, Any]) -> list[str]:
    """Return configured end phrases (primary + alternates), in order."""
    if gates.get("end_phrases"):
        return [str(p) for p in gates["end_phrases"] if str(p).strip()]
    if gates.get("end_phrase"):
        return [str(gates["end_phrase"])]
    return []


def start_countdown_tokens_from_gates(gates: dict[str, Any]) -> list[str]:
    tokens = gates.get("start_countdown_tokens")
    if tokens is None:
        tokens = gates.get("start_phrase_countdown_tokens")
    if not tokens:
        return []
    return [str(t).strip().lower() for t in tokens if str(t).strip()]


def start_countdown_suffix_from_gates(gates: dict[str, Any]) -> list[str]:
    suffix = gates.get("start_countdown_suffix_tokens")
    if suffix is None:
        suffix = gates.get("start_phrase_countdown_suffix")
    if not suffix:
        return []
    return [str(t).strip().lower() for t in suffix if str(t).strip()]


def start_trigger_phrase_from_gates(gates: dict[str, Any]) -> str:
    trigger = str(gates.get("start_trigger_phrase") or "").strip()
    if trigger:
        return trigger
    legacy = str(gates.get("start_phrase") or "").strip()
    if not legacy:
        return ""
    countdown = start_countdown_tokens_from_gates(gates)
    if countdown and any(t in legacy.lower() for t in countdown):
        return _split_legacy_combined_start_phrase(legacy, countdown)
    return legacy


def start_countdown_allow_in_from_gates(gates: dict[str, Any]) -> bool:
    value = gates.get("start_countdown_allow_in")
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def format_countdown_hint(
    countdown_tokens: list[str],
    *,
    allow_in: bool = True,
) -> str:
    if not countdown_tokens:
        return ""
    spoken = " ".join(countdown_tokens)
    if allow_in:
        return f'in {spoken} (optional "in"; numbers may be skipped)'
    return f"{spoken} (numbers may be skipped)"


def format_start_phrase_display(gates: dict[str, Any]) -> str:
    """Single-line display of trigger + optional countdown (legacy callers)."""
    trigger = start_trigger_phrase_from_gates(gates)
    countdown = start_countdown_tokens_from_gates(gates)
    if not trigger:
        return ""
    if not countdown:
        return trigger
    allow_in = start_countdown_allow_in_from_gates(gates)
    hint = format_countdown_hint(countdown, allow_in=allow_in)
    return f'{trigger}, then {hint}'


def phrase_gates_path(repo_root: Path | None = None) -> Path:
    root = (repo_root or REPO_ROOT).resolve()
    return root / PHRASE_GATES_FILENAME


def _normalize_gates(raw: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(EMBEDDED_DEFAULTS)
    for key in _STATE_OVERRIDE_KEYS:
        if key not in raw or raw[key] is None:
            continue
        out[key] = raw[key]

    unpause = out.get("unpause_phrases") or out.get("unpause_phrase")
    if isinstance(unpause, str):
        out["unpause_phrases"] = [unpause]
    elif isinstance(unpause, list):
        out["unpause_phrases"] = [str(p) for p in unpause if str(p).strip()]
    out.pop("unpause_phrase", None)

    end_phrases: list[str] = []
    if raw.get("end_phrases"):
        end_phrases.extend(str(p) for p in raw["end_phrases"] if str(p).strip())
    elif out.get("end_phrases"):
        end_phrases.extend(str(p) for p in out["end_phrases"] if str(p).strip())
    if raw.get("end_phrase") and str(raw["end_phrase"]).strip():
        primary = str(raw["end_phrase"]).strip()
        if primary not in end_phrases:
            end_phrases.insert(0, primary)
    if end_phrases:
        out["end_phrases"] = end_phrases
    out.pop("end_phrase", None)

    flag_phrases: list[str] = []
    if raw.get("flag_phrases"):
        flag_phrases.extend(str(p) for p in raw["flag_phrases"] if str(p).strip())
    elif out.get("flag_phrases"):
        flag_phrases.extend(str(p) for p in out["flag_phrases"] if str(p).strip())
    if raw.get("flag_phrase") and str(raw["flag_phrase"]).strip():
        primary = str(raw["flag_phrase"]).strip()
        if primary not in flag_phrases:
            flag_phrases.insert(0, primary)
    if flag_phrases:
        out["flag_phrases"] = flag_phrases
    out.pop("flag_phrase", None)

    # Countdown tokens: prefer new keys, fall back to legacy names.
    countdown = start_countdown_tokens_from_gates(out)
    if countdown:
        out["start_countdown_tokens"] = countdown
    out.pop("start_phrase_countdown_tokens", None)

    suffix = start_countdown_suffix_from_gates(out)
    if suffix:
        out["start_countdown_suffix_tokens"] = suffix
    out.pop("start_phrase_countdown_suffix", None)

    trigger = start_trigger_phrase_from_gates(out)
    if trigger:
        out["start_trigger_phrase"] = trigger

    # Legacy combined start_phrase for callers that still read it.
    out["start_phrase"] = format_start_phrase_display(out)

    return out


def load_phrase_gates(
    *,
    repo_root: Path | None = None,
    state_overrides: dict | None = None,
    create_file_if_missing: bool = True,
) -> dict[str, Any]:
    """
    Load phrase gates: embedded defaults <- JSON file <- optional state overrides.

    When ``create_file_if_missing`` and the JSON file is absent, write
    ``podcast-phrase-gates.json`` with embedded defaults.
    """
    path = phrase_gates_path(repo_root)
    merged = deepcopy(EMBEDDED_DEFAULTS)
    if path.is_file():
        file_data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(file_data, dict):
            raise ValueError(f"{path} must contain a JSON object.")
        merged = _normalize_gates({**merged, **file_data})
    elif create_file_if_missing:
        save_phrase_gates(merged, repo_root=repo_root)

    if state_overrides:
        patch = {
            key: state_overrides[key]
            for key in _STATE_OVERRIDE_KEYS
            if key in state_overrides and state_overrides[key] is not None
        }
        if patch:
            merged = _normalize_gates({**merged, **patch})
    else:
        merged = _normalize_gates(merged)
    return merged


def save_phrase_gates(
    updates: dict[str, Any],
    *,
    repo_root: Path | None = None,
) -> Path:
    """Merge ``updates`` into the on-disk phrase gates file and save."""
    path = phrase_gates_path(repo_root)
    current = load_phrase_gates(
        repo_root=repo_root,
        create_file_if_missing=False,
    )
    merged = _normalize_gates({**current, **updates})
    # Persist canonical new keys; omit legacy derived start_phrase.
    to_save = {k: v for k, v in merged.items() if k != "start_phrase"}
    path.write_text(json.dumps(to_save, indent=2) + "\n", encoding="utf-8")
    return path


def podcast_phrase_cli_args(state: dict | None = None) -> list[str]:
    """CLI args for ``generate_full_dsl.py`` from shared gates (+ optional state)."""
    gates = load_phrase_gates(state_overrides=state or {})
    out: list[str] = []

    trigger = start_trigger_phrase_from_gates(gates)
    if trigger:
        out.extend(["--start-trigger-phrase", trigger])
        if gates.get("start_preroll_sec") is not None:
            out.extend(["--start-preroll-sec", str(gates["start_preroll_sec"])])

    countdown = start_countdown_tokens_from_gates(gates)
    if countdown:
        out.extend(["--start-phrase-countdown", *[str(t) for t in countdown]])
        suffix = start_countdown_suffix_from_gates(gates)
        if suffix:
            out.extend(["--start-phrase-countdown-suffix", *[str(t) for t in suffix]])
        if not start_countdown_allow_in_from_gates(gates):
            out.append("--no-start-countdown-in")

    end_phrases = end_phrases_from_gates(gates)
    for phrase in end_phrases:
        out.extend(["--end-phrase", phrase])
    if end_phrases and gates.get("end_postroll_sec") is not None:
        out.extend(["--end-postroll-sec", str(gates["end_postroll_sec"])])

    pause_phrase = gates.get("pause_phrase")
    if pause_phrase:
        out.extend(["--pause-phrase", str(pause_phrase)])
        for phrase in gates.get("unpause_phrases") or []:
            out.extend(["--unpause-phrase", str(phrase)])
        if gates.get("pause_preroll_sec") is not None:
            out.extend(["--pause-preroll-sec", str(gates["pause_preroll_sec"])])
        if gates.get("pause_postroll_sec") is not None:
            out.extend(["--pause-postroll-sec", str(gates["pause_postroll_sec"])])

    abort_phrase = gates.get("abort_phrase")
    if abort_phrase:
        out.extend(["--abort-phrase", str(abort_phrase)])

    return out


def apply_namespace_phrase_defaults(args: Any) -> Any:
    """
    Fill missing ``generate_full_dsl.py`` phrase args from shared gates.

    Explicit CLI values on ``args`` win over the file.
    """
    gates = load_phrase_gates()
    if getattr(args, "start_trigger_phrase", None) in (None, ""):
        if getattr(args, "start_phrase", None) not in (None, ""):
            args.start_trigger_phrase = str(args.start_phrase)
        else:
            args.start_trigger_phrase = start_trigger_phrase_from_gates(gates) or None
    if getattr(args, "start_phrase_countdown", None) is None:
        tokens = start_countdown_tokens_from_gates(gates)
        args.start_phrase_countdown = list(tokens) if tokens else []
    if getattr(args, "start_phrase_countdown_suffix", None) is None:
        suffix = start_countdown_suffix_from_gates(gates)
        args.start_phrase_countdown_suffix = list(suffix) if suffix else []
    if getattr(args, "start_countdown_allow_in", None) is None:
        args.start_countdown_allow_in = start_countdown_allow_in_from_gates(gates)
    if not start_countdown_allow_in_from_gates(gates):
        args.no_start_countdown_in = True
    if not getattr(args, "end_phrase", None):
        args.end_phrase = end_phrases_from_gates(gates)
    if getattr(args, "pause_phrase", None) in (None, ""):
        args.pause_phrase = gates.get("pause_phrase")
    if not getattr(args, "unpause_phrase", None):
        args.unpause_phrase = list(gates.get("unpause_phrases") or [])
    if getattr(args, "abort_phrase", None) in (None, ""):
        args.abort_phrase = gates.get("abort_phrase")
    if getattr(args, "start_preroll_sec", None) is None:
        args.start_preroll_sec = float(gates.get("start_preroll_sec", 1.0))
    if getattr(args, "end_postroll_sec", None) is None:
        args.end_postroll_sec = float(gates.get("end_postroll_sec", 1.0))
    if getattr(args, "pause_preroll_sec", None) is None:
        args.pause_preroll_sec = float(gates.get("pause_preroll_sec", 0.25))
    if getattr(args, "pause_postroll_sec", None) is None:
        args.pause_postroll_sec = float(gates.get("pause_postroll_sec", 0.7))
    return args
