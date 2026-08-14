"""Canonical entity resolver for transcript-derived export surfaces.

Raw transcripts stay untouched. Corrections are applied only to derived
surfaces (summary, task suggestions), high-confidence only, with an audit
artifact so every rewrite is inspectable.
"""

import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .config import (
    VAULT,
    ENTITY_HIGH_CONFIDENCE,
    ENTITY_MEDIUM_CONFIDENCE,
)

WIKI_SOURCES = ("wiki/entities", "wiki/concepts")
GLOSSARY_PATHS = ("docs/glossary.txt", "docs/glossary.json")

# Common words that must never be treated as correctable proper nouns,
# even when capitalized at sentence start.
STOPWORDS = frozenset(
    """a an and are as at be been but by can could did do does for from had has have
    he her hers him his how i if in into is it its may me might must my no not of on
    or our ours out over own said she should so some such than that the their theirs
    them then there these they this those through to too under until up us was we
    were what when where which while who whom why will with would you your yours
    about after all also any because before between both during each few more most
    new only other same still then very vai vamos para por com uma que nao sim esta
    esse isso aqui todo todos toda todas ser estar tem temos foi era sao seja mais
    menos muito pouco onde qual quando quem porque como entre sobre ate ja sim nao
    monday tuesday wednesday thursday friday saturday sunday janeiro fevereiro marco
    abril maio junho julho agosto setembro outubro novembro dezembro january february
    march april may june july august september october november december meeting
    meetcap obsidian vault notes note tasks task day today week month year action
    item items follow followup follow-ups key points summary transcript recording
   """.split()
)


def normalize_term(text: str) -> str:
    """Lowercase, de-accent, and collapse whitespace for canonical matching."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def slug_to_display(slug: str) -> str:
    """Convert a wiki filename slug into a display name ('project-tachyon' → 'Project Tachyon')."""
    words = re.sub(r"\.md$", "", slug).replace("-", " ").replace("_", " ").split()
    return " ".join(word.capitalize() for word in words)


def load_vocabulary(
    vault_root: Path | str | None = None,
    glossary_paths: list[Path | str] | None = None,
    participants: list[str] | None = None,
) -> dict[str, Any]:
    """Build the canonical vocabulary: {'canonical': [...], 'aliases': {normalized_alias: canonical}}.

    Sources: vault wiki slugs (entities + concepts), optional glossary files
    (txt: one term or 'alias = canonical' per line; json: {'terms': [...],
    'aliases': {...}}), and explicit participant names. Missing sources are
    skipped silently.
    """
    vault = Path(vault_root) if vault_root is not None else Path(VAULT)
    canonical: list[str] = []
    aliases: dict[str, str] = {}

    def register(term: str) -> None:
        term = " ".join(str(term).strip().split())
        if len(term) < 3:
            return
        normalized = normalize_term(term)
        if normalized and normalized not in {normalize_term(c) for c in canonical}:
            canonical.append(term)

    def register_alias(alias: str, target: str) -> None:
        alias = " ".join(str(alias).strip().split())
        target = " ".join(str(target).strip().split())
        if len(alias) < 3 or not target:
            return
        aliases[normalize_term(alias)] = target
        register(target)

    for relative in WIKI_SOURCES:
        wiki_dir = vault / relative
        if not wiki_dir.is_dir():
            continue
        for md_file in sorted(wiki_dir.glob("*.md")):
            display = slug_to_display(md_file.name)
            register(display)

    paths = glossary_paths
    if paths is None:
        paths = [vault / p for p in GLOSSARY_PATHS]
    for glossary_path in paths:
        glossary = Path(glossary_path)
        if not glossary.is_file():
            continue
        if glossary.suffix == ".json":
            try:
                payload = json.loads(glossary.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for term in payload.get("terms", []) if isinstance(payload, dict) else []:
                if isinstance(term, str):
                    register(term)
            alias_map = payload.get("aliases", {}) if isinstance(payload, dict) else {}
            if isinstance(alias_map, dict):
                for alias, target in alias_map.items():
                    if isinstance(target, str):
                        register_alias(alias, target)
        else:
            try:
                lines = glossary.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                if "=" in line:
                    alias, _, target = line.partition("=")
                    register_alias(alias, target)
                else:
                    register(line)

    for participant in participants or []:
        if isinstance(participant, str):
            register(participant)

    return {"canonical": canonical, "aliases": aliases}


CANDIDATE_PATTERN = re.compile(r"\b[A-Z][\w’'-]*(?:\s+[A-Z][\w’'-]*)*\b")


def extract_candidates(text: str) -> list[str]:
    """Extract suspicious proper-noun surfaces from derived text."""
    candidates: list[str] = []
    seen: set[str] = set()
    for match in CANDIDATE_PATTERN.finditer(text):
        surface = match.group(0).strip()
        words = surface.split()
        # Drop surfaces whose every word is a stopword (sentence-start noise).
        if all(word.lower().strip("’'-") in STOPWORDS for word in words):
            continue
        if len(surface) < 4:
            continue
        if surface not in seen:
            seen.add(surface)
            candidates.append(surface)
    return candidates


def score_candidate(
    surface: str,
    vocabulary: dict[str, Any],
) -> tuple[float, str, str] | None:
    """Score one surface against the vocabulary.

    Returns (confidence, rule, canonical) or None when nothing clears the
    medium threshold. Already-canonical surfaces return None.
    """
    normalized = normalize_term(surface)
    if not normalized:
        return None

    canonical_names = vocabulary.get("canonical", [])
    canonical_set = {normalize_term(name) for name in canonical_names}
    if normalized in canonical_set:
        return None

    alias_target = vocabulary.get("aliases", {}).get(normalized)
    if alias_target:
        return (1.0, "alias", alias_target)

    best: tuple[float, str, str] | None = None
    for name in canonical_names:
        normalized_name = normalize_term(name)
        if not normalized_name:
            continue
        if normalized_name in normalized or normalized in normalized_name:
            if min(len(normalized), len(normalized_name)) >= 5:
                candidate = (0.9, "substring", name)
                if best is None or candidate[0] > best[0]:
                    best = candidate
                continue
        ratio = SequenceMatcher(None, normalized, normalized_name).ratio()
        if ratio >= ENTITY_MEDIUM_CONFIDENCE:
            candidate = (ratio, "fuzzy", name)
            if best is None or candidate[0] > best[0]:
                best = candidate

    if best is None or best[0] < ENTITY_MEDIUM_CONFIDENCE:
        return None
    return best


def resolve_entities(text: str, vocabulary: dict[str, Any]) -> list[dict[str, Any]]:
    """Find corrections for suspicious surfaces in derived text.

    Only surfaces that clear the medium threshold are reported; confidence
    and rule are included so callers can decide what to auto-apply.
    Multiword surfaces that match nothing are retried without their leading
    word, because sentence-start words often merge into the surface
    ("Discussed Jiracash" → "Jiracash").
    """
    corrections: list[dict[str, Any]] = []
    for surface in extract_candidates(text):
        match = score_candidate(surface, vocabulary)
        if match is None:
            words = surface.split()
            while match is None and len(words) > 1:
                words = words[1:]
                match = score_candidate(" ".join(words), vocabulary)
                if match is not None:
                    surface = " ".join(words)
        if match is None:
            continue
        confidence, rule, canonical = match
        corrections.append({
            "surface": surface,
            "canonical": canonical,
            "confidence": round(confidence, 3),
            "rule": rule,
            "auto_applied": confidence >= ENTITY_HIGH_CONFIDENCE,
        })
    return corrections


def apply_corrections(text: str, corrections: list[dict[str, Any]]) -> str:
    """Apply high-confidence corrections to derived text with word boundaries."""
    for correction in corrections:
        if not correction.get("auto_applied"):
            continue
        surface = correction["surface"]
        canonical = correction["canonical"]
        pattern = re.compile(r"\b" + re.escape(surface) + r"\b")
        text = pattern.sub(canonical, text)
    return text


def render_name_corrections(corrections: list[dict[str, Any]]) -> str:
    """Render the '## Name Corrections' audit block for the exported note."""
    if not corrections:
        return ""
    lines = ["## Name Corrections", ""]
    for correction in corrections:
        label = "high confidence" if correction["auto_applied"] else "flagged, not rewritten"
        lines.append(
            f"- `{correction['surface']}` -> `{correction['canonical']}` ({label}, {correction['rule']})"
        )
    return "\n".join(lines)


def resolve_derived_surfaces(
    text: str,
    vocabulary: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    """Convenience wrapper: correct derived text and return the audit list."""
    corrections = resolve_entities(text, vocabulary)
    return apply_corrections(text, corrections), corrections
