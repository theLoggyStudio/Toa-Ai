"""Recherche contextuelle sur les scans pour Toa (sans spoil)."""

from __future__ import annotations

import logging
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from models import TextBlock
from services.translation import _chat, is_translator_available

logger = logging.getLogger(__name__)

CHIBIE_RESEARCH_VISION_SYSTEM = """Tu analyses des pages de manga/manhwa SCANNEES pour aider Toa,
mascotte de Toa AI, a commenter la lecture — SANS SPOILER.

Tu ne dois utiliser QUE ce qui est VISIBLE sur les images fournies (texte, logos, numeros de chapitre,
etiquettes de noms, credits, titre de serie sur la page).

Interdit:
- Utiliser tes connaissances externes sur l'intrigue si ce n'est pas ecrit/dessine sur le scan.
- Deviner la suite de l'histoire, les twists, ou ce qui arrive plus tard.
- Inventer des noms de personnages non mentionnes ou non etiquetes sur ces pages.

Extrais au maximum:
- Titre probable du manga/serie (logo, en-tete, copyright).
- Indice de chapitre/volume/page (numero visible).
- Indice d'arc/saga UNIQUEMENT si un libelle l'indique explicitement sur le scan.
- Noms de personnages: etiquettes (name tags), noms ecrits en marge, ou appeles clairement dans les bulles visibles.
- Notes visuelles utiles (fantrad, magazine, langue du scan) — 1 courte phrase max.

Si tu n'es pas sur, ecris "inconnu" pour ce champ.

Format STRICT (une ligne par cle, pas de markdown):
MANGA|titre ou inconnu
CHAPTER|indice ou inconnu
ARC|indice ou inconnu
CHARACTERS|Nom1,Nom2 (virgules, vide si aucun)
NOTES|phrase courte ou rien"""

CHIBIE_RESEARCH_PAGE_TEXT_SYSTEM = """Tu enrichis le contexte de Toa a partir du texte DEJA traduit d'UNE page lue.

Regles anti-spoil:
- N'ajoute que des noms explicitement presents dans les bulles de cette page.
- Pas de deduction sur l'avenir de l'histoire.
- Pas de connaissances externes sur le manga.

Si un champ n'apporte rien de nouveau, omets la ligne.

Format (lignes optionnelles uniquement):
CHARACTERS_ADD|Nom1,Nom2
CHAPTER|indice si nouveau
ARC|indice si nouveau et visible dans le texte de la page
NOTES|precision courte si utile"""


@dataclass
class ChibieScanContext:
    manga_title: str = ""
    chapter_hint: str = ""
    arc_hint: str = ""
    characters: list[str] = field(default_factory=list)
    visual_notes: str = ""
    pages_analyzed: int = 0

    def merge_parsed(self, data: dict[str, str]) -> None:
        manga = _clean_field(data.get("MANGA", ""))
        if manga and manga.lower() != "inconnu":
            if not self.manga_title or self.manga_title.lower() == "inconnu":
                self.manga_title = manga

        chapter = _clean_field(data.get("CHAPTER", ""))
        if chapter and chapter.lower() != "inconnu":
            if not self.chapter_hint or self.chapter_hint.lower() == "inconnu":
                self.chapter_hint = chapter

        arc = _clean_field(data.get("ARC", ""))
        if arc and arc.lower() != "inconnu":
            if not self.arc_hint:
                self.arc_hint = arc
            elif arc.lower() not in self.arc_hint.lower():
                self.arc_hint = f"{self.arc_hint}; {arc}"

        for key in ("CHARACTERS", "CHARACTERS_ADD"):
            raw = data.get(key, "")
            if raw:
                self._add_characters(raw)

        notes = _clean_field(data.get("NOTES", ""))
        if notes and notes.lower() not in ("rien", "inconnu", "-"):
            if self.visual_notes:
                if notes not in self.visual_notes:
                    self.visual_notes = f"{self.visual_notes} | {notes}"
            else:
                self.visual_notes = notes

    def _add_characters(self, raw: str) -> None:
        for part in re.split(r"[,;|/]", raw):
            name = part.strip()
            if not name or len(name) < 2:
                continue
            if name.lower() in ("inconnu", "unknown", "n/a"):
                continue
            if name not in self.characters:
                self.characters.append(name)

    def to_prompt_block(self) -> str:
        lines = ["Contexte scan (sans spoil, visible sur les pages deja lues):"]
        lines.append(
            f"- Serie: {self.manga_title or 'inconnu'}"
        )
        lines.append(
            f"- Chapitre/page: {self.chapter_hint or 'inconnu'}"
        )
        if self.arc_hint:
            lines.append(f"- Arc/saga (indice): {self.arc_hint}")
        if self.characters:
            shown = ", ".join(self.characters[:12])
            extra = len(self.characters) - 12
            if extra > 0:
                shown += f" (+{extra})"
            lines.append(f"- Personnages repères: {shown}")
        if self.visual_notes:
            lines.append(f"- Notes: {self.visual_notes}")
        lines.append(
            "- Ne devine pas la suite; ne spoile pas au-dela de ce qui est deja lu."
        )
        return "\n".join(lines)


def _clean_field(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _parse_research_response(raw: str) -> dict[str, str]:
    data: dict[str, str] = {}
    for line in raw.strip().splitlines():
        line = line.strip()
        if "|" not in line:
            continue
        key, _, val = line.partition("|")
        key = key.strip().upper()
        val = val.strip()
        if key and val:
            data[key] = val
    return data


def research_initial_scan_context(
    image_paths: list[Path],
    *,
    session_id: str,
    max_pages: int = 3,
) -> ChibieScanContext:
    """Vision sur les premieres pages pour titre, chapitre, personnages visibles."""
    ctx = ChibieScanContext()
    if not is_translator_available() or not image_paths:
        return ctx

    samples = image_paths[:max_pages]
    prompt = (
        f"Analyse {len(samples)} page(s) scannee(s) jointe(s).\n"
        "Extrais titre serie, chapitre, arc (si libelle visible), personnages, notes.\n"
        "Aucun spoil : uniquement ce qui est visible sur ces images."
    )
    request_id = f"{session_id}-chibie-research-init-{secrets.token_hex(3)}"
    try:
        raw = _chat(
            prompt,
            request_id,
            system_prompt=CHIBIE_RESEARCH_VISION_SYSTEM,
            images=samples,
        )
        ctx.merge_parsed(_parse_research_response(raw))
        ctx.pages_analyzed = len(samples)
    except Exception as exc:
        logger.warning("Toa recherche initiale: %s", exc)
    return ctx


def research_page_from_blocks(
    ctx: ChibieScanContext,
    *,
    page_index: int,
    blocks: list[TextBlock],
    session_id: str,
) -> ChibieScanContext:
    """Enrichit le contexte via le texte traduit de la page (noms cites, indices)."""
    if not is_translator_available() or not blocks:
        ctx.pages_analyzed = max(ctx.pages_analyzed, page_index + 1)
        return ctx

    lines = [f"Page {page_index + 1} — bulles traduites:"]
    for block in blocks[:14]:
        tr = (block.translatedText or "").strip()
        for tag in ("[[DIR:V]]", "[[DIR:H]]", "[[BG:SOLID]]", "[[BG:TRANSPARENT]]"):
            tr = tr.replace(tag, "")
        if tr:
            lines.append(f"- {tr[:140]}")
    if len(lines) < 2:
        ctx.pages_analyzed = max(ctx.pages_analyzed, page_index + 1)
        return ctx

    known = ", ".join(ctx.characters[:20]) if ctx.characters else "(aucun)"
    prompt = (
        f"Contexte deja connu — Serie: {ctx.manga_title or 'inconnu'}, "
        f"Chapitre: {ctx.chapter_hint or 'inconnu'}, "
        f"Personnages: {known}\n\n"
        f"{chr(10).join(lines)}\n\n"
        "Ajoute uniquement des noms ou indices NOUVEAUX et explicites sur cette page."
    )
    request_id = f"{session_id}-chibie-research-p{page_index}-{secrets.token_hex(3)}"
    try:
        raw = _chat(prompt, request_id, system_prompt=CHIBIE_RESEARCH_PAGE_TEXT_SYSTEM)
        ctx.merge_parsed(_parse_research_response(raw))
    except Exception as exc:
        logger.warning("Toa recherche page %s: %s", page_index, exc)
    ctx.pages_analyzed = max(ctx.pages_analyzed, page_index + 1)
    return ctx
