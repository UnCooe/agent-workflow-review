from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from session_review.safety import stable_hash
from session_review.transcripts.schemas import ArtifactHashes, TurnPaths, TurnRef


@dataclass(frozen=True)
class TranscriptRunPaths:
    root: Path

    @property
    def facts_turn_cards(self) -> Path:
        return self.root / "facts" / "turns" / "cards"

    @property
    def facts_turn_full(self) -> Path:
        return self.root / "facts" / "turns" / "full"

    @property
    def facts_turn_json(self) -> Path:
        return self.root / "facts" / "turns" / "json"

    @property
    def facts_sessions(self) -> Path:
        return self.root / "facts" / "sessions"

    @property
    def aggregate(self) -> Path:
        return self.root / "aggregate"

    @property
    def subject_mcp(self) -> Path:
        return self.root / "subjects" / "mcp"

    @property
    def agent(self) -> Path:
        return self.root / "agent"

    def ensure(self) -> None:
        for path in (
            self.facts_turn_cards,
            self.facts_turn_full,
            self.facts_turn_json,
            self.facts_sessions,
            self.aggregate,
            self.subject_mcp,
            self.agent,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def turn_paths(self, turn_id: str) -> tuple[Path, Path, Path]:
        return (
            self.facts_turn_cards / f"{turn_id}.md",
            self.facts_turn_full / f"{turn_id}.md",
            self.facts_turn_json / f"{turn_id}.json",
        )

    def relative_turn_paths(self, turn_id: str) -> TurnPaths:
        card, full, json_path = self.turn_paths(turn_id)
        return TurnPaths(
            card_md=_rel(card, self.root),
            full_md=_rel(full, self.root),
            json=_rel(json_path, self.root),
        )


def make_turn_ref(
    *,
    run_paths: TranscriptRunPaths,
    turn_id: str,
    session_id_hash: str,
    fact_json: str,
    card_md: str,
    full_md: str,
    bucket_flags: list[str] | None = None,
) -> TurnRef:
    return TurnRef(
        turn_id=turn_id,
        paths=run_paths.relative_turn_paths(turn_id),
        hashes=ArtifactHashes(
            fact_json=stable_hash(fact_json),
            card_md=stable_hash(card_md),
            full_md=stable_hash(full_md),
        ),
        session_id_hash=session_id_hash,
        bucket_flags=bucket_flags or [],
    )


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()
