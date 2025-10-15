from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import logging
from functools import lru_cache
from pathlib import Path
import re
from typing import Iterable

from app.core.config import settings

try:  # pragma: no cover - optional dependency
    import openai  # type: ignore
except ImportError:  # pragma: no cover - guard for environments without openai
    openai = None  # type: ignore

logger = logging.getLogger(__name__)

SECTION_PATTERN = re.compile(r"^(#{1,6})\s+(.*)", re.MULTILINE)


@dataclass(frozen=True)
class HelpSnippet:
    path: str
    heading: str
    content: str


@dataclass(frozen=True)
class HelpMatch:
    path: str
    heading: str
    snippet: str
    score: float


class HelpIndex:
    """Lightweight document retriever for chat help answers."""

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.docs_dir = root_dir / "docs"
        self._snippets: list[HelpSnippet] = self._load_snippets()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def search(self, query: str, *, limit: int = 3) -> list[HelpMatch]:
        normalized_query = (query or "").strip()
        if not normalized_query:
            return []

        tokens = [token for token in re.split(r"\W+", normalized_query.lower()) if token]
        if not tokens:
            return []

        matches: list[HelpMatch] = []
        for snippet in self._snippets:
            score = self._score_snippet(snippet, tokens, normalized_query.lower())
            if score <= 0:
                continue
            condensed = self._condense(snippet.content)
            matches.append(
                HelpMatch(
                    path=snippet.path,
                    heading=snippet.heading,
                    snippet=condensed,
                    score=score,
                )
            )

        matches.sort(key=lambda item: item.score, reverse=True)
        return matches[:limit]

    def generate_answer(self, query: str, matches: Iterable[HelpMatch]) -> tuple[str, bool]:
        matches = list(matches)
        if not matches:
            return (
                "I could not find a matching help topic. Try rephrasing your question or check the onboarding docs.",
                False,
            )

        answer = self._compose_local_answer(query, matches)
        if settings.enable_openai and openai is not None and settings.openai_api_key:
            llm_answer = self._compose_llm_answer(query, matches)
            if llm_answer:
                return llm_answer, True
        return answer, False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _load_snippets(self) -> list[HelpSnippet]:
        snippets: list[HelpSnippet] = []
        for path in self._candidate_paths():
            try:
                content = path.read_text(encoding="utf-8")
            except OSError as exc:  # pragma: no cover - defensive
                logger.debug("Skipping help doc %s: %s", path, exc)
                continue

            relative = str(path.relative_to(self.root_dir))
            for heading, body in self._split_sections(content):
                cleaned = body.strip()
                if not cleaned:
                    continue
                snippets.append(
                    HelpSnippet(
                        path=relative,
                        heading=heading,
                        content=cleaned,
                    )
                )
        return snippets

    def _candidate_paths(self) -> list[Path]:
        root_files = ["README.md", "AGENTS.md", "HOW_TO_USE.md"]
        paths: list[Path] = []
        for filename in root_files:
            candidate = self.root_dir / filename
            if candidate.exists():
                paths.append(candidate)

        if self.docs_dir.exists():
            for doc in sorted(self.docs_dir.glob("*.md")):
                paths.append(doc)
        return paths

    @staticmethod
    def _split_sections(text: str) -> list[tuple[str, str]]:
        sections: list[tuple[str, str]] = []
        current_heading = "Overview"
        current_lines: list[str] = []

        for line in text.splitlines():
            match = SECTION_PATTERN.match(line)
            if match:
                heading_level = len(match.group(1))
                heading_text = match.group(2).strip()
                if heading_level <= 3:
                    if current_lines:
                        sections.append((current_heading, "\n".join(current_lines).strip()))
                    current_heading = heading_text or current_heading
                    current_lines = []
                    continue
            current_lines.append(line)

        if current_lines:
            sections.append((current_heading, "\n".join(current_lines).strip()))
        return sections

    @staticmethod
    def _condense(text: str, *, max_length: int = 320) -> str:
        flattened = " ".join(fragment.strip() for fragment in text.splitlines() if fragment.strip())
        if len(flattened) <= max_length:
            return flattened

        sentence_end = re.search(r"([.!?])\s", flattened)
        if sentence_end and sentence_end.end() <= max_length:
            return flattened[: sentence_end.end()].strip()
        return f"{flattened[: max_length - 1].rstrip()}…"

    @staticmethod
    def _score_snippet(snippet: HelpSnippet, tokens: list[str], query_lower: str) -> float:
        text_lower = snippet.content.lower()
        heading_lower = snippet.heading.lower()
        token_hits = sum(1 for token in tokens if token in text_lower)
        heading_hits = sum(1 for token in tokens if token in heading_lower)
        similarity = SequenceMatcher(None, query_lower, text_lower[:500]).ratio()
        return token_hits * 2 + heading_hits * 1.5 + similarity

    def _compose_local_answer(self, query: str, matches: list[HelpMatch]) -> str:
        top = matches[0]
        additional = matches[1:]
        parts = [top.snippet]
        if additional:
            references = ", ".join(f"{match.heading} ({match.path})" for match in additional)
            parts.append(f"Related: {references}.")
        parts.append(f"Primary source: {top.heading} ({top.path}).")
        return " ".join(parts)

    def _compose_llm_answer(self, query: str, matches: list[HelpMatch]) -> str | None:
        if openai is None:  # pragma: no cover - protective
            return None

        prompt_sections = "\n\n".join(
            f"Source: {match.path}\nHeading: {match.heading}\nSnippet: {match.snippet}"
            for match in matches
        )

        messages = [
            {
                "role": "system",
                "content": (
                    "You are Pricebot's support assistant. Answer questions using the provided documentation "
                    "snippets. Include citations with the source path in parentheses."
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"Question: {query}\n\nDocumentation:\n{prompt_sections}"},
                ],
            },
        ]

        try:  # pragma: no cover - network/runtime path
            client = openai.OpenAI(api_key=settings.openai_api_key)
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0,
                max_tokens=400,
                messages=messages,
            )
            answer = response.choices[0].message.content or ""
            return answer.strip() or None
        except Exception as exc:
            logger.warning("LLM help answer failed, falling back to local summary: %s", exc)
            return None


@lru_cache(maxsize=1)
def get_help_index() -> HelpIndex:
    root_dir = Path(__file__).resolve().parents[2]
    return HelpIndex(root_dir)


def reset_help_index_cache() -> None:
    """TEST-ONLY: clear cached help index."""

    get_help_index.cache_clear()
