from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .models import AccessPolicy, RawRef, SensitivityLevel, SourceType


SECRET_KEY_RE = re.compile(r"(token|secret|api[_-]?key|password|authorization|cookie)", re.I)
IDENTIFIER_KEY_RE = re.compile(
    r"(request_id|trace_id|tts_id|message_id|user_id|role_id|identifier|session_id)", re.I
)
TEXT_KEY_RE = re.compile(r"(text|content|prompt|message|transcript|output|input)", re.I)
PATH_KEY_RE = re.compile(r"(path|cwd|file|filename|cmd|command)", re.I)
MAX_SUMMARY_CHARS = 160


def stable_hash(value: object, *, length: int = 16) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:length]


def summarize_text(value: object, *, max_chars: int = MAX_SUMMARY_CHARS) -> str:
    text = str(value or "")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def classify_key_value(key: str, value: object) -> SensitivityLevel:
    if SECRET_KEY_RE.search(key):
        return SensitivityLevel.S3
    if TEXT_KEY_RE.search(key):
        return SensitivityLevel.S2
    if IDENTIFIER_KEY_RE.search(key) or PATH_KEY_RE.search(key):
        return SensitivityLevel.S1
    if isinstance(value, str) and SECRET_KEY_RE.search(value):
        return SensitivityLevel.S3
    return SensitivityLevel.S0


def max_sensitivity(levels: list[SensitivityLevel | str]) -> SensitivityLevel:
    order = {
        SensitivityLevel.S0: 0,
        SensitivityLevel.S1: 1,
        SensitivityLevel.S2: 2,
        SensitivityLevel.S3: 3,
    }
    normalized = [
        item if isinstance(item, SensitivityLevel) else SensitivityLevel(str(item))
        for item in (levels or [SensitivityLevel.S0])
    ]
    return max(normalized, key=lambda item: order[item])


class SafetyPolicy:
    def __init__(
        self,
        *,
        include_raw_text: bool = False,
        hash_identifiers: bool = True,
        default_ttl_days: int = 30,
    ) -> None:
        self.include_raw_text = include_raw_text
        self.hash_identifiers = hash_identifiers
        self.default_ttl_days = default_ttl_days
        self.redaction_warnings: list[str] = []

    def raw_ref(
        self,
        *,
        source_type: SourceType = SourceType.CODEX_JSONL,
        source_id: object,
        locator: dict[str, Any],
        sensitivity: SensitivityLevel = SensitivityLevel.S0,
    ) -> RawRef:
        return RawRef(
            source_type=source_type,
            source_id_hash=stable_hash(source_id),
            locator=self._safe_locator(locator),
            sensitivity=sensitivity,
            ttl_days=self.default_ttl_days,
            access_policy=self._access_policy(sensitivity),
        )

    def redact_mapping(self, value: dict[str, Any] | None) -> tuple[dict[str, Any], SensitivityLevel]:
        if not value:
            return {}, SensitivityLevel.S0
        output: dict[str, Any] = {}
        levels: list[SensitivityLevel] = []
        for key, raw in value.items():
            level = classify_key_value(str(key), raw)
            levels.append(level)
            output[str(key)] = self._redact_value(str(key), raw, level)
        return output, max_sensitivity(levels)

    def summarize_output(self, value: object) -> tuple[dict[str, Any], SensitivityLevel]:
        text = "" if value is None else str(value)
        payload = _extract_json(text)
        if isinstance(payload, dict):
            summary: dict[str, Any] = {"top_level_keys": sorted(str(key) for key in payload)[:10]}
            for key in ("total", "total_hits", "returned", "total_returned", "candidate_count"):
                if key in payload and isinstance(payload[key], int):
                    summary[key] = payload[key]
            for key in ("logs", "samples", "candidates", "timeline"):
                item = payload.get(key)
                if isinstance(item, list):
                    summary[f"{key}_count"] = len(item)
            _, level = self.redact_mapping({key: payload[key] for key in list(payload)[:10]})
            return summary, level
        if not text:
            return {}, SensitivityLevel.S0
        if self.include_raw_text:
            return {"text_preview": summarize_text(text)}, SensitivityLevel.S2
        return {"text_hash": stable_hash(text), "char_count": len(text)}, SensitivityLevel.S2

    def _redact_value(self, key: str, value: Any, level: SensitivityLevel) -> Any:
        if level == SensitivityLevel.S3:
            self.redaction_warnings.append(f"redacted_secret:{key}")
            return "[redacted:S3]"
        if level == SensitivityLevel.S2 and not self.include_raw_text:
            return {"hash": stable_hash(value), "kind": "text", "chars": len(str(value or ""))}
        if level == SensitivityLevel.S1 and self.hash_identifiers:
            return {"hash": stable_hash(value), "kind": _s1_kind(key)}
        if isinstance(value, str):
            return summarize_text(value)
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return {"hash": stable_hash(value), "kind": type(value).__name__}

    def _safe_locator(self, locator: dict[str, Any]) -> dict[str, Any]:
        safe: dict[str, Any] = {}
        for key, value in locator.items():
            if key in {"line", "record_index", "turn_index"}:
                safe[key] = value
            elif key in {"file", "path"}:
                safe["file_hash"] = stable_hash(str(value))
                safe["suffix"] = Path(str(value)).suffix
            else:
                safe[str(key)] = stable_hash(value)
        return safe

    @staticmethod
    def _access_policy(sensitivity: SensitivityLevel) -> AccessPolicy:
        if sensitivity in {SensitivityLevel.S2, SensitivityLevel.S3}:
            return AccessPolicy.RESTRICTED
        if sensitivity == SensitivityLevel.S1:
            return AccessPolicy.SHAREABLE_REDACTED
        return AccessPolicy.LOCAL_ONLY


def _s1_kind(key: str) -> str:
    if PATH_KEY_RE.search(key):
        return "path_or_command"
    return "identifier"


def _extract_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return None
