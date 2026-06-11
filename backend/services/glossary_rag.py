"""RAG dictionnaire local : JMdict (japonais) + Kengdic (coréen-anglais).

Les dictionnaires sont téléchargés une fois dans backend/data/glossaries/.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from config import DATA_DIR

logger = logging.getLogger(__name__)

GLOSSARY_DIR = DATA_DIR / "glossaries"
KENGDIC_DB = GLOSSARY_DIR / "kengdic.db"
KENGDIC_TSV_URL = (
    "https://raw.githubusercontent.com/garfieldnate/kengdic/master/kengdic.tsv"
)
JMDICT_DB = GLOSSARY_DIR / "jmdict.sqlite"
JMDICT_SQLITE_URL = (
    "https://github.com/seanmcbroom/JMdictSQLite/releases/download/latest/jmdict.sqlite"
)
MANGA_DB = GLOSSARY_DIR / "manga_glossary.db"

_jamdict_instance = None
_jamdict_unavailable: str | None = None

# Entrées manga / onomatopées (prioritaires sur JMdict)
MANGA_SEED: dict[tuple[str, str, str], str] = {
    ("ja", "fr", "ゴロゴロ"): "Prrr…",
    ("ja", "fr", "ゴロゴ"): "Prrr…",
    ("ja", "fr", "ゴロ"): "Prrr…",
    ("ja", "fr", "にゃー"): "Miaou !",
    ("ja", "fr", "ニャー"): "Miaou !",
    ("ja", "fr", "にゃ"): "Miaou",
    ("ja", "fr", "わかった"): "Compris !",
    ("ja", "fr", "分かった"): "Compris !",
    ("ja", "fr", "大人買い"): "achat en bloc",
    ("ja", "fr", "大人買いだー"): "Quel achat en bloc !",
    ("ja", "fr", "大人買いだ"): "Quel achat en bloc !",
    ("ja", "fr", "これ全部箱ごとください"): "Je prends tout, boîtes comprises.",
    ("ja", "fr", "これ全部"): "Je prends tout.",
    ("ja", "fr", "箱ごと"): "boîtes comprises",
    ("ja", "fr", "ずんっ"): "Boum !",
    ("ja", "fr", "ぎゃああああ"): "GYAAAA !",
    ("ja", "fr", "ぎゃあああ"): "GYAAAA !",
    ("ja", "en", "ゴロゴロ"): "Purr…",
    ("ja", "en", "にゃー"): "Meow!",
    ("ko", "fr", "냥"): "Miaou !",
    ("ko", "fr", "야옹"): "Miaou !",
    ("ko", "en", "냥"): "Meow!",
}


@dataclass
class GlossaryHit:
    term: str
    reading: str
    gloss: str
    dictionary: str
    score: float


def is_glossary_rag_enabled() -> bool:
    load_dotenv(override=True)
    return os.getenv("GLOSSARY_RAG_ENABLED", "true").lower() in (
        "true",
        "1",
        "yes",
    )


def _download_file(url: str, dest: Path, timeout: int = 600) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    logger.info("Téléchargement dictionnaire : %s", url)
    req = urllib.request.Request(url, headers={"User-Agent": "ToaAI/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    tmp.write_bytes(data)
    tmp.replace(dest)


def _ensure_manga_db() -> sqlite3.Connection:
    GLOSSARY_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(MANGA_DB)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS manga_terms (
            source_lang TEXT NOT NULL,
            target_lang TEXT NOT NULL,
            source_text TEXT NOT NULL,
            translation TEXT NOT NULL,
            PRIMARY KEY (source_lang, target_lang, source_text)
        )
        """
    )
    for (sl, tl, src), tr in MANGA_SEED.items():
        conn.execute(
            "INSERT OR IGNORE INTO manga_terms VALUES (?, ?, ?, ?)",
            (sl, tl, src, tr),
        )
    conn.commit()
    return conn


def _lookup_manga(
    query: str, source_lang: str, target_lang: str
) -> GlossaryHit | None:
    q = query.strip()
    if not q:
        return None
    conn = _ensure_manga_db()
    row = conn.execute(
        """
        SELECT source_text, translation FROM manga_terms
        WHERE source_lang = ? AND target_lang = ? AND source_text = ?
        """,
        (source_lang, target_lang, q),
    ).fetchone()
    conn.close()
    if row:
        return GlossaryHit(
            term=row[0],
            reading="",
            gloss=row[1],
            dictionary="manga",
            score=1.0,
        )
    for (sl, tl, src), tr in MANGA_SEED.items():
        if sl == source_lang and tl == target_lang and (
            q.startswith(src) or src.startswith(q)
        ):
            return GlossaryHit(
                term=q,
                reading="",
                gloss=tr,
                dictionary="manga",
                score=0.92,
            )
    return None


def _build_kengdic_db(tsv_path: Path, db_path: Path) -> None:
    logger.info("Construction index Kengdic (~11 Mo, 1-2 min)…")
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE entries (
            id INTEGER PRIMARY KEY,
            korean TEXT NOT NULL,
            hanja TEXT,
            english TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE entries_fts USING fts5(
            korean, english, hanja,
            content='entries', content_rowid='id'
        )
        """
    )
    batch: list[tuple[str, str, str]] = []
    with tsv_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            korean = (row.get("surface") or "").strip()
            english = (row.get("gloss") or "").strip()
            hanja = (row.get("hanja") or "").strip()
            if not korean or not english:
                continue
            batch.append((korean, hanja, english))
            if len(batch) >= 5000:
                conn.executemany(
                    "INSERT INTO entries (korean, hanja, english) VALUES (?, ?, ?)",
                    batch,
                )
                batch.clear()
        if batch:
            conn.executemany(
                "INSERT INTO entries (korean, hanja, english) VALUES (?, ?, ?)",
                batch,
            )
    conn.execute("INSERT INTO entries_fts(entries_fts) VALUES('rebuild')")
    conn.commit()
    conn.close()
    logger.info("Index Kengdic prêt : %s", db_path)


def _ensure_kengdic_db() -> sqlite3.Connection | None:
    if KENGDIC_DB.exists():
        return sqlite3.connect(KENGDIC_DB)
    GLOSSARY_DIR.mkdir(parents=True, exist_ok=True)
    tsv_path = GLOSSARY_DIR / "kengdic.tsv"
    if not tsv_path.exists():
        try:
            _download_file(KENGDIC_TSV_URL, tsv_path)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning("Kengdic indisponible : %s", exc)
            return None
    try:
        _build_kengdic_db(tsv_path, KENGDIC_DB)
    except OSError as exc:
        logger.warning("Échec construction Kengdic : %s", exc)
        return None
    return sqlite3.connect(KENGDIC_DB)


def _lookup_kengdic(query: str, limit: int = 5) -> list[GlossaryHit]:
    conn = _ensure_kengdic_db()
    if conn is None:
        return []
    q = query.strip()
    hits: list[GlossaryHit] = []
    try:
        rows = conn.execute(
            """
            SELECT korean, hanja, english, bm25(entries_fts) AS rank
            FROM entries_fts
            WHERE entries_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (q, limit),
        ).fetchall()
        for korean, hanja, english, rank in rows:
            gloss = english.strip()
            if hanja:
                gloss = f"{gloss} ({hanja})"
            hits.append(
                GlossaryHit(
                    term=korean,
                    reading=hanja or "",
                    gloss=gloss,
                    dictionary="kengdic",
                    score=max(0.1, 1.0 / (1.0 + abs(float(rank or 0)))),
                )
            )
        if not hits:
            rows = conn.execute(
                """
                SELECT korean, hanja, english FROM entries
                WHERE korean = ? OR korean LIKE ?
                LIMIT ?
                """,
                (q, f"{q}%", limit),
            ).fetchall()
            for korean, hanja, english in rows:
                gloss = (english or "").strip()
                if hanja:
                    gloss = f"{gloss} ({hanja})"
                hits.append(
                    GlossaryHit(
                        term=korean,
                        reading=hanja or "",
                        gloss=gloss,
                        dictionary="kengdic",
                        score=0.85,
                    )
                )
    finally:
        conn.close()
    return hits


def _get_jamdict():
    global _jamdict_instance, _jamdict_unavailable
    if _jamdict_unavailable:
        return None
    if _jamdict_instance is not None:
        return _jamdict_instance
    try:
        from jamdict import Jamdict

        jam = Jamdict()
        if not jam.ready:
            _jamdict_unavailable = "base JMdict non prête"
            return None
        _jamdict_instance = jam
        return jam
    except ImportError:
        _jamdict_unavailable = "pip install jamdict jamdict-data"
        return None
    except Exception as exc:
        _jamdict_unavailable = str(exc)
        return None


def _glosses_from_jm_entry(entry) -> str:
    parts: list[str] = []
    for sense in getattr(entry, "senses", []) or []:
        for g in getattr(sense, "gloss", []) or []:
            text = getattr(g, "text", None) or str(g)
            if text and text not in parts:
                parts.append(text)
    return "; ".join(parts[:4])


def _ensure_jmdict_sqlite() -> sqlite3.Connection | None:
    if not JMDICT_DB.exists():
        try:
            _download_file(JMDICT_SQLITE_URL, JMDICT_DB, timeout=900)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning("JMdict SQLite indisponible : %s", exc)
            return None
    return sqlite3.connect(JMDICT_DB)


def _parse_written_json(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        return []
    out: list[str] = []
    for item in items:
        if isinstance(item, dict) and item.get("written"):
            out.append(str(item["written"]))
    return out


def _lookup_jmdict_sqlite(query: str, limit: int = 5) -> list[GlossaryHit]:
    conn = _ensure_jmdict_sqlite()
    if conn is None:
        return []
    q = query.strip()
    hits: list[GlossaryHit] = []
    seen: set[str] = set()
    try:
        rows = conn.execute(
            """
            SELECT e.kanji, e.kana, s.glosses
            FROM entries e
            JOIN senses s ON s.ent_seq = e.ent_seq
            WHERE EXISTS (
                SELECT 1 FROM json_each(e.kana) AS kn
                WHERE json_extract(kn.value, '$.written') = ?
                   OR json_extract(kn.value, '$.written') LIKE ?
            )
               OR EXISTS (
                SELECT 1 FROM json_each(e.kanji) AS kj
                WHERE json_extract(kj.value, '$.written') = ?
                   OR json_extract(kj.value, '$.written') LIKE ?
            )
            LIMIT ?
            """,
            (q, f"{q}%", q, f"{q}%", limit * 4),
        ).fetchall()
        for kanji_raw, kana_raw, glosses_raw in rows:
            kanji_list = _parse_written_json(kanji_raw)
            kana_list = _parse_written_json(kana_raw)
            term = (kanji_list[0] if kanji_list else "") or (
                kana_list[0] if kana_list else q
            )
            reading = kana_list[0] if kana_list else ""
            try:
                gloss_list = json.loads(glosses_raw or "[]")
            except json.JSONDecodeError:
                gloss_list = []
            gloss = "; ".join(str(g) for g in gloss_list[:4] if g)
            if not gloss:
                continue
            key = f"{term}|{reading}|{gloss}"
            if key in seen:
                continue
            seen.add(key)
            hits.append(
                GlossaryHit(
                    term=term,
                    reading=reading,
                    gloss=gloss,
                    dictionary="jmdict",
                    score=0.95 if term == q or reading == q else 0.8,
                )
            )
    finally:
        conn.close()
    return hits[:limit]


def _lookup_jamdict_package(query: str, limit: int = 5) -> list[GlossaryHit]:
    jam = _get_jamdict()
    if jam is None:
        return []
    q = query.strip()
    hits: list[GlossaryHit] = []
    queries = [q]
    if len(q) >= 2 and not q.endswith("%"):
        queries.append(f"{q}%")
    seen: set[str] = set()
    for lookup_q in queries:
        try:
            result = jam.lookup(lookup_q, strict_lookup=False)
        except Exception as exc:
            logger.debug("jamdict lookup %s : %s", lookup_q, exc)
            continue
        for entry in (getattr(result, "entries", None) or [])[:limit]:
            kanji = ""
            if getattr(entry, "kanji_forms", None):
                kanji = entry.kanji_forms[0].text
            reading = ""
            if getattr(entry, "kana_forms", None):
                reading = entry.kana_forms[0].text
            gloss = _glosses_from_jm_entry(entry)
            if not gloss:
                continue
            key = f"{kanji}|{reading}|{gloss}"
            if key in seen:
                continue
            seen.add(key)
            term = kanji or reading or q
            hits.append(
                GlossaryHit(
                    term=term,
                    reading=reading,
                    gloss=gloss,
                    dictionary="jmdict",
                    score=0.95 if term == q or reading == q else 0.75,
                )
            )
        if hits:
            break
    return hits[:limit]


def _lookup_jamdict(query: str, limit: int = 5) -> list[GlossaryHit]:
    hits = _lookup_jamdict_package(query, limit=limit)
    if hits:
        return hits
    return _lookup_jmdict_sqlite(query, limit=limit)


def retrieve_hits(
    query: str,
    source_lang: str,
    target_lang: str,
    *,
    limit: int = 5,
) -> list[GlossaryHit]:
    """Récupère les entrées de dictionnaire les plus pertinentes (RAG)."""
    if not is_glossary_rag_enabled():
        return []
    q = query.strip()
    if not q:
        return []

    hits: list[GlossaryHit] = []
    manga = _lookup_manga(q, source_lang, target_lang)
    if manga:
        hits.append(manga)

    if source_lang == "ja":
        hits.extend(_lookup_jamdict(q, limit=limit))
    elif source_lang == "ko":
        hits.extend(_lookup_kengdic(q, limit=limit))

    hits.sort(key=lambda h: h.score, reverse=True)
    deduped: list[GlossaryHit] = []
    seen_gloss: set[str] = set()
    for h in hits:
        key = h.gloss.lower()[:80]
        if key in seen_gloss:
            continue
        seen_gloss.add(key)
        deduped.append(h)
        if len(deduped) >= limit:
            break
    return deduped


def try_direct_translation(
    query: str,
    source_lang: str,
    target_lang: str,
) -> str | None:
    """Traduction directe si entrée manga / dictionnaire très fiable."""
    if not is_glossary_rag_enabled():
        return None
    manga = _lookup_manga(query, source_lang, target_lang)
    if manga and manga.score >= 0.9:
        return manga.gloss
    return None


def format_rag_context(
    hits: list[GlossaryHit],
    source_lang: str,
    target_lang: str,
) -> str:
    if not hits:
        return ""
    lang_map = {"ja": "japonais", "ko": "coréen", "fr": "français", "en": "anglais"}
    tgt = lang_map.get(target_lang, target_lang)
    lines = [
        f"Références dictionnaire ({lang_map.get(source_lang, source_lang)} → {tgt}).",
        "Utilise-les comme sens de base ; adapte au style manga / bulle :",
    ]
    for i, h in enumerate(hits, 1):
        reading = f" [{h.reading}]" if h.reading else ""
        lines.append(f"{i}. {h.term}{reading} — {h.gloss} ({h.dictionary})")
    if target_lang == "fr":
        lines.append(
            "Les glosses en anglais doivent être reformulées en français naturel."
        )
    return "\n".join(lines)


def get_glossary_status() -> dict:
    """État des dictionnaires pour /health."""
    load_dotenv(override=True)
    status: dict = {
        "enabled": is_glossary_rag_enabled(),
        "mangaDb": MANGA_DB.exists(),
        "kengdicReady": KENGDIC_DB.exists(),
        "jmdictSqliteReady": JMDICT_DB.exists(),
        "jamdictPackageReady": False,
        "jamdictPackageError": _jamdict_unavailable,
    }
    jam = _get_jamdict()
    if jam is not None:
        status["jamdictPackageReady"] = bool(jam.ready)
        status["jamdictPackageError"] = None
    return status


def preload_dictionaries() -> dict:
    """Télécharge / indexe les dictionnaires (appel manuel ou au démarrage)."""
    result = {
        "manga": True,
        "kengdic": False,
        "jmdictSqlite": False,
        "jamdictPackage": False,
    }
    _ensure_manga_db()
    conn = _ensure_kengdic_db()
    if conn:
        conn.close()
        result["kengdic"] = KENGDIC_DB.exists()
    jm = _ensure_jmdict_sqlite()
    if jm:
        jm.close()
        result["jmdictSqlite"] = JMDICT_DB.exists()
    jam = _get_jamdict()
    if jam and jam.ready:
        result["jamdictPackage"] = True
    return result
