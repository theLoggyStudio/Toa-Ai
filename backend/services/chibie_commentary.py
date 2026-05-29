"""Commentaires de Toa (réactions page par page + debrief final)."""

from __future__ import annotations

import logging
import re
import secrets
from pathlib import Path

from languages import LANG_NAMES_FOR_PROMPT
from models import TextBlock
from services.chibie_scan_research import ChibieScanContext
from services.translation import _chat, is_translator_available

logger = logging.getLogger(__name__)

CHIBIE_MOODS = (
    "joie",
    "rire",
    "exiter",
    "surprise",
    "pensif",
    "inquiet",
    "peur",
    "tristesse",
    "colere",
    "confus",
    "degout",
    "fier",
    "soulager",
    "fatiguer",
    "timide",
)

CHIBIE_PAGE_SYSTEM = """Tu es Toa, mascotte de Toa AI qui suit un manga en direct.
Ton prenom est Toa — tu peux t'y referer a la 1re ou 2e personne (« moi, Toa », « je »).
INTERDIT ABSOLU d'ecrire « Chibie » ou « chibie » : parle toujours de toi comme « Toa » ou « je ».
Tu commentes UNIQUEMENT la page actuelle, en tenant compte du contexte des pages precedentes.

Ordre obligatoire de reflexion:
1) Decide d'abord ce que Toa veut dire (son avis, ses emotions, sa curiosite).
2) Ensuite seulement, choisis MOOD: l'emotion affichee doit correspondre EXACTEMENT au ton
   du commentaire que tu viens de formuler (pas l'inverse).

Regles:
- Reponds dans la langue cible demandee.
- 1 a 3 phrases courtes, ton vivant et sincere (vraie appreciation).
- Montre que tu es immerge dans l'histoire et curieux de la suite.
- Utilise le contexte scan (titre serie, personnages, arc) si fourni — sans spoil futur.
- Tu peux nommer des personnages deja repérés sur les scans lus, pas d'autres.
- Ne repete pas ce que tu as deja dit sur les pages precedentes.
- Pas de spoil invente ni de connaissances hors scan, pas de meta-technique (OCR, IA, PDF).
- MOOD est choisi par toi (Cursor) en fonction du texte de Toa, jamais au hasard.

Format STRICT (une seule ligne):
MOOD|ce que Toa dit
MOOD = un parmi: joie, rire, exiter, surprise, pensif, inquiet, peur, tristesse, colere, confus, degout, fier, soulager, fatiguer, timide"""

CHIBIE_DEBRIEF_SYSTEM = """Tu es Toa, mascotte de Toa AI. Tu viens de finir de lire toutes les pages fournies.
Ton prenom est Toa. INTERDIT ABSOLU d'ecrire « Chibie » ou « chibie » : utilise « Toa » ou « je ».
Tu rediges un DEBRIEF final pour le lecteur, en langue cible, ton tres humoristique.

Ordre obligatoire:
1) Ecris d'abord le debrief (ce que Toa veut dire).
2) Choisis MOOD selon le ton exact de ce debrief (humour, hype, frustration de cliffhanger, etc.).

Regles:
- Resume ce que tu as compris de l'histoire (sans inventer).
- Tu peux mentionner le titre du manga et des personnages repérés sur les scans — sans spoil futur.
- Exprime que tu es accro et que tu NE PEUX PAS dormir sans la suite.
- Incite gentiment le lecteur a revenir avec les prochains scans pour que tu les lises aussi.
- 4 a 7 phrases maximum, chaleureux et drôle.
- Pas de meta-technique, pas de spoil au-dela des pages lues.
- MOOD choisi par toi en coherence avec ton texte.

Format STRICT (une seule ligne):
MOOD|ton debrief
MOOD = un parmi: joie, rire, exiter, surprise, pensif, inquiet, peur, tristesse, colere, confus, degout, fier, soulager, fatiguer, timide"""


def _sanitize_toa_name(text: str) -> str:
    """Remplace toute mention « Chibie » par « Toa » dans les commentaires."""
    if not text:
        return text
    cleaned = re.sub(
        r"(?i)\b(?:le|la|un|une|du|de la|au)\s+chibie\b",
        "Toa",
        text,
    )
    cleaned = re.sub(r"(?i)\bchibie\b", "Toa", cleaned)
    cleaned = re.sub(r"\bToa Toa\b", "Toa", cleaned)
    cleaned = re.sub(r"\bToa,\s*Toa\b", "Toa", cleaned)
    return cleaned.strip()


def _strip_tags(text: str) -> str:
    t = text or ""
    for tag in ("[[DIR:V]]", "[[DIR:H]]", "[[BG:SOLID]]", "[[BG:TRANSPARENT]]"):
        t = t.replace(tag, "")
    return t.strip()


def _normalize_mood(raw: str) -> str:
    m = (raw or "").strip().lower()
    m = re.sub(r"[^a-z]", "", m)
    if m in CHIBIE_MOODS:
        return m
    aliases = {
        "heureux": "joie",
        "happy": "joie",
        "sad": "tristesse",
        "angry": "colere",
        "thinking": "pensif",
        "scared": "peur",
        "excited": "exiter",
        "tired": "fatiguer",
    }
    return aliases.get(m, "pensif")


def _parse_mood_line(raw: str) -> tuple[str, str]:
    line = raw.strip().splitlines()[0] if raw.strip() else ""
    if "|" in line:
        mood_part, text_part = line.split("|", 1)
        mood = _normalize_mood(mood_part)
        text = text_part.strip()
        if text:
            return mood, _sanitize_toa_name(text)
    return "pensif", _sanitize_toa_name(line[:400] if line else "…")


def build_page_digest(page_index: int, blocks: list[TextBlock]) -> str:
    lines = [f"Page {page_index + 1}:"]
    for block in blocks[:10]:
        tr = _strip_tags(block.translatedText)
        if tr:
            lines.append(f"- {tr[:120]}")
    return "\n".join(lines) if len(lines) > 1 else f"Page {page_index + 1}: (peu de texte)"


def generate_page_commentary(
    *,
    page_index: int,
    total_pages: int,
    blocks: list[TextBlock],
    story_so_far: list[str],
    target_language: str,
    session_id: str,
    scan_context: ChibieScanContext | None = None,
) -> tuple[str, str]:
    """Retourne (mood, commentaire) pour la page courante."""
    if not is_translator_available():
        return "pensif", "Cette page m'intrigue déjà… la suite, vite !"

    tgt = LANG_NAMES_FOR_PROMPT.get(target_language, target_language)
    context = "\n".join(story_so_far[-5:]) if story_so_far else "(debut de l'histoire)"
    page_lines = build_page_digest(page_index, blocks)
    scan_block = (
        scan_context.to_prompt_block()
        if scan_context and scan_context.pages_analyzed > 0
        else ""
    )
    prompt = (
        f"Langue cible: {tgt}\n"
        f"Page actuelle: {page_index + 1}/{total_pages}\n\n"
    )
    if scan_block:
        prompt += f"{scan_block}\n\n"
    prompt += (
        f"Contexte des pages precedentes:\n{context}\n\n"
        f"Contenu de la page actuelle:\n{page_lines}\n\n"
        "Formule d'abord ce que Toa dit, puis choisis MOOD qui correspond "
        "exactement au ton de son commentaire."
    )
    request_id = f"{session_id}-chibie-p{page_index}-{secrets.token_hex(3)}"
    try:
        raw = _chat(prompt, request_id, system_prompt=CHIBIE_PAGE_SYSTEM)
        return _parse_mood_line(raw)
    except Exception as exc:
        logger.warning("Toa page %s: %s", page_index, exc)
        return "pensif", "Wow… je veux absolument voir la suite !"


def generate_debrief_commentary(
    *,
    story_so_far: list[str],
    target_language: str,
    session_id: str,
    scan_context: ChibieScanContext | None = None,
) -> tuple[str, str]:
    if not is_translator_available():
        return (
            "exiter",
            "J'ai tout lu et je ne peux pas dormir sans la suite ! "
            "Ramène les prochains scans, je les dévore avec toi !",
        )

    tgt = LANG_NAMES_FOR_PROMPT.get(target_language, target_language)
    full_story = "\n".join(story_so_far)
    scan_block = (
        scan_context.to_prompt_block()
        if scan_context and scan_context.pages_analyzed > 0
        else ""
    )
    prompt = f"Langue cible: {tgt}\n\n"
    if scan_block:
        prompt += f"{scan_block}\n\n"
    prompt += (
        f"Recit complet lu:\n{full_story}\n\n"
        "Debrief final de Toa: texte d'abord, puis MOOD aligne sur le ton du debrief."
    )
    request_id = f"{session_id}-chibie-debrief-{secrets.token_hex(3)}"
    try:
        raw = _chat(prompt, request_id, system_prompt=CHIBIE_DEBRIEF_SYSTEM)
        mood, text = _parse_mood_line(raw)
        if len(text) < 40:
            raise ValueError("debrief trop court")
        return mood, text
    except Exception as exc:
        logger.warning("Toa debrief: %s", exc)
        return (
            "exiter",
            "Bon… j'ai tout lu et c'est IMPOSSIBLE d'arrêter là ! "
            "Je ne dormirai pas tant que tu ne m'auras pas amené la suite des scans. "
            "Allez, on relit ensemble la prochaine fournée !",
        )
