"""Rapport des transformations OCR/traduction par image (affichage disque)."""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path

import os

from dotenv import load_dotenv

from config import OUTPUT_DIR
from models import TextBlock
from services.translation import is_translator_available

logger = logging.getLogger(__name__)


def _report_path(task_id: str) -> Path:
    return OUTPUT_DIR / task_id / "transformations.json"


def sort_blocks_reading_order(blocks: list[TextBlock]) -> list[TextBlock]:
    """Ordre de lecture manga : haut → bas, gauche → droite."""
    return sorted(
        blocks,
        key=lambda b: (b.boundingBox.y_min, b.boundingBox.x_min),
    )


def build_page_entry(
    image_path: Path,
    page_index: int,
    blocks: list[TextBlock],
) -> dict:
    ordered = sort_blocks_reading_order(blocks)
    return {
        "pageIndex": page_index,
        "imageName": image_path.name,
        "bubbles": [
            {
                "order": i + 1,
                "originalText": block.originalText,
                "translatedText": block.translatedText,
                "boundingBox": block.boundingBox.model_dump(),
            }
            for i, block in enumerate(ordered)
        ],
    }


def append_page(
    task_id: str,
    *,
    source_language: str,
    target_language: str,
    page_entry: dict,
) -> None:
    path = _report_path(task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        report = json.loads(path.read_text(encoding="utf-8"))
    else:
        report = {
            "taskId": task_id,
            "sourceLanguage": source_language,
            "targetLanguage": target_language,
            "pages": [],
        }
    report["pages"] = [p for p in report["pages"] if p["pageIndex"] != page_entry["pageIndex"]]
    report["pages"].append(page_entry)
    report["pages"].sort(key=lambda p: p["pageIndex"])
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


def init_report(task_id: str, source_language: str, target_language: str) -> None:
    path = _report_path(task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "taskId": task_id,
        "sourceLanguage": source_language,
        "targetLanguage": target_language,
        "pages": [],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def load_report(task_id: str) -> dict | None:
    path = _report_path(task_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_reports() -> list[dict]:
    """Taches ayant un rapport, de la plus recente a la plus ancienne."""
    items: list[dict] = []
    if not OUTPUT_DIR.exists():
        return items
    for task_dir in OUTPUT_DIR.iterdir():
        if not task_dir.is_dir():
            continue
        path = task_dir / "transformations.json"
        if not path.is_file():
            continue
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        items.append(
            {
                "taskId": report.get("taskId", task_dir.name),
                "mtime": path.stat().st_mtime,
                "pageCount": len(report.get("pages", [])),
                "report": report,
            }
        )
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def find_latest_report() -> dict | None:
    reports = list_reports()
    if not reports:
        return None
    return reports[0]["report"]


def build_status_banner(report: dict | None) -> str:
    del report
    translator_ok = is_translator_available()
    translator_class = "status-pill--ok" if translator_ok else "status-pill--err"
    translator_label = (
        "Cursor API connectée" if translator_ok else "Cursor API hors ligne"
    )

    warnings: list[str] = []
    if not translator_ok:
        warnings.append(
            "CURSOR_API_KEY manquant ou invalide. Configurez la clé Cursor "
            "dans backend/.env."
        )

    warn_html = ""
    if warnings:
        items = "".join(f"<li>{_html_escape(w)}</li>" for w in warnings)
        warn_html = f'<ul class="status-banner__warn">{items}</ul>'

    return f"""
    <aside class="status-banner" role="status">
      <span class="status-pill">Traduction 100 % Cursor</span>
      <span class="status-pill {translator_class}">{_html_escape(translator_label)}</span>
      {warn_html}
    </aside>
    """


def _task_is_active(task_id: str) -> bool:
    from services.storage import get_task

    task = get_task(task_id)
    return task is not None and task.status in ("processing", "paid")


def _truncate(text: str, max_len: int = 48) -> str:
    one_line = " ".join(text.split())
    if len(one_line) <= max_len:
        return one_line
    return one_line[: max_len - 1] + "…"


def log_disk_report(task_id: str, page_entry: dict) -> None:
    """Affiche dans la console un disque ASCII par image."""
    n = len(page_entry["bubbles"])
    name = page_entry["imageName"]
    lines = [
        "",
        "=" * 56,
        f"  DISQUE  {name}  ({n} bulle(s))",
        "=" * 56,
    ]
    if n == 0:
        lines.append("  (aucune bulle)")
    else:
        radius = max(12, min(20, 8 + n * 2))
        for bubble in page_entry["bubbles"]:
            angle = (2 * math.pi * (bubble["order"] - 1) / n) - math.pi / 2
            x = int(radius * math.cos(angle))
            y = int(radius * math.sin(angle) * 0.5)
            pad = " " * max(0, 18 + x)
            lines.append(f"{pad}#{bubble['order']} ── position ({x:+3d}, {y:+3d})")
            lines.append(f"{'':20}  SRC : {_truncate(bubble['originalText'])}")
            lines.append(f"{'':20}  TRG : {_truncate(bubble['translatedText'])}")
            lines.append("")
    lines.append(f"  Tâche {task_id[:8]}…")
    lines.append("=" * 56)
    logger.info("\n".join(lines))


def render_empty_home() -> str:
    banner = build_status_banner(None)
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta http-equiv="refresh" content="5" />
  <title>Toa AI — Transformations</title>
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: "Segoe UI", system-ui, sans-serif;
      background: #2a241c;
      color: #f2ebe1;
      padding: 2rem;
      max-width: 52rem;
      margin-inline: auto;
    }}
    p {{ opacity: 0.85; line-height: 1.5; }}
    .status-banner {{
      background: #4a3f35;
      border: 2px solid #d2c5b8;
      border-radius: 8px;
      padding: 0.75rem 1rem;
      margin: 1rem 0 1.5rem;
    }}
    .status-pill {{
      display: inline-block;
      font-size: 0.75rem;
      padding: 0.25rem 0.55rem;
      margin-right: 0.5rem;
      border-radius: 4px;
      background: #d2c5b8;
      color: #2a241c;
    }}
    .status-pill--ok {{ background: #8fbc8f; }}
    .status-pill--err {{ background: #e8b4b8; }}
    .status-banner__warn {{
      margin: 0.5rem 0 0;
      padding-left: 1.2rem;
      font-size: 0.82rem;
      color: #f5d9a8;
    }}
  </style>
</head>
<body>
  <h1>Toa AI — Transformations</h1>
  {banner}
  <p>Aucune traduction pour le moment. Lancez une tâche depuis le frontend ; cette page se mettra à jour automatiquement.</p>
</body>
</html>"""


def render_home_page(task_id: str | None = None) -> str:
    """Page d'accueil : derniere tache (ou ?task=id), rafraichissement si en cours."""
    report: dict | None
    if task_id:
        report = load_report(task_id)
    else:
        report = find_latest_report()

    if not report:
        return render_empty_home()

    auto_refresh = _task_is_active(report["taskId"])
    all_tasks = list_reports()
    return render_html_report(
        report,
        auto_refresh=auto_refresh,
        task_nav=all_tasks,
        is_home=True,
    )


def render_html_report(
    report: dict,
    *,
    auto_refresh: bool = False,
    task_nav: list[dict] | None = None,
    is_home: bool = False,
) -> str:
    """Vue HTML : un disque par image, bulles alignées en cercle."""
    pages_html: list[str] = []
    for page in report.get("pages", []):
        bubbles = page.get("bubbles", [])
        n = max(len(bubbles), 1)
        items: list[str] = []
        for bubble in bubbles:
            i = bubble["order"] - 1
            angle_deg = (360 / n) * i
            items.append(
                f"""
                <article class="disk__bubble" style="--angle: {angle_deg}deg">
                  <span class="disk__bubble-order">#{bubble["order"]}</span>
                  <p class="disk__bubble-src">{_html_escape(bubble["originalText"])}</p>
                  <p class="disk__bubble-trg">{_html_escape(bubble["translatedText"])}</p>
                </article>
                """
            )
        pages_html.append(
            f"""
            <section class="disk" aria-label="Image { _html_escape(page["imageName"]) }">
              <div class="disk__ring" style="--count: {n}">
                {''.join(items)}
              </div>
              <div class="disk__hub">
                <span class="disk__hub-label">Image</span>
                <strong class="disk__hub-name">{_html_escape(page["imageName"])}</strong>
                <span class="disk__hub-meta">{len(bubbles)} bulle(s)</span>
              </div>
            </section>
            """
        )

    task_short = report.get("taskId", "")[:8]
    current_id = report.get("taskId", "")
    refresh_tag = (
        '  <meta http-equiv="refresh" content="3" />\n' if auto_refresh else ""
    )
    status_banner = build_status_banner(report)
    nav_html = ""
    if task_nav and len(task_nav) > 1:
        links = []
        for entry in task_nav:
            tid = entry["taskId"]
            short = tid[:8]
            active = " is-active" if tid == current_id else ""
            href = "/" if tid == current_id and is_home else f"/?task={tid}"
            links.append(
                f'<a class="task-nav__link{active}" href="{href}">'
                f"{short}… ({entry['pageCount']} img)</a>"
            )
        nav_html = f'<nav class="task-nav" aria-label="Tâches">{"".join(links)}</nav>'

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
{refresh_tag}  <title>Toa AI — Transformations {task_short}</title>
  <style>
    :root {{
      --bg: #2a241c;
      --disk: #d2c5b8;
      --hub: #4a3f35;
      --text: #f2ebe1;
      --accent: #e8b4b8;
      --border: #2a241c;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", system-ui, sans-serif;
      background: var(--bg);
      color: var(--text);
      padding: 1.5rem;
    }}
    h1 {{
      font-size: 1.25rem;
      margin: 0 0 0.25rem;
    }}
    .meta {{
      color: #c4b8aa;
      margin-bottom: 2rem;
      font-size: 0.9rem;
    }}
    .gallery {{
      display: flex;
      flex-wrap: wrap;
      gap: 2.5rem;
      justify-content: center;
      align-items: flex-start;
    }}
    .disk {{
      position: relative;
      width: min(92vw, 380px);
      height: min(92vw, 380px);
      margin: 0 auto;
    }}
    .disk__ring {{
      position: absolute;
      inset: 0;
      border-radius: 50%;
      background: radial-gradient(circle at 50% 50%, #e8dfd4 0%, var(--disk) 55%, #b8a99a 100%);
      border: 4px solid var(--border);
      box-shadow: 4px 4px 0 var(--border);
    }}
    .disk__hub {{
      position: absolute;
      top: 50%;
      left: 50%;
      transform: translate(-50%, -50%);
      width: 42%;
      aspect-ratio: 1;
      border-radius: 50%;
      background: var(--hub);
      border: 3px solid var(--border);
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      text-align: center;
      padding: 0.5rem;
      z-index: 2;
    }}
    .disk__hub-label {{
      font-size: 0.65rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      opacity: 0.85;
    }}
    .disk__hub-name {{
      font-size: 0.72rem;
      word-break: break-all;
      line-height: 1.3;
      margin: 0.25rem 0;
      color: var(--accent);
    }}
    .disk__hub-meta {{
      font-size: 0.7rem;
      opacity: 0.8;
    }}
    .disk__bubble {{
      position: absolute;
      top: 50%;
      left: 50%;
      width: 118px;
      margin-left: -59px;
      margin-top: -2.5rem;
      transform: rotate(var(--angle)) translateY(calc(-1 * min(42vw, 165px)))
                 rotate(calc(-1 * var(--angle)));
      background: #fff;
      color: #2a241c;
      border: 2px solid var(--border);
      border-radius: 8px;
      padding: 0.35rem 0.45rem;
      font-size: 0.62rem;
      line-height: 1.25;
      box-shadow: 2px 2px 0 var(--border);
      z-index: 1;
    }}
    .disk__bubble-order {{
      display: inline-block;
      font-weight: 700;
      color: var(--hub);
      margin-bottom: 0.15rem;
    }}
    .disk__bubble-src {{
      margin: 0 0 0.2rem;
      opacity: 0.85;
    }}
    .disk__bubble-trg {{
      margin: 0;
      font-weight: 600;
      color: #6b3d42;
    }}
    .empty {{
      text-align: center;
      opacity: 0.7;
      padding: 3rem;
    }}
    .task-nav {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
      margin-bottom: 1.25rem;
    }}
    .task-nav__link {{
      color: var(--text);
      text-decoration: none;
      font-size: 0.8rem;
      padding: 0.35rem 0.65rem;
      border: 2px solid var(--disk);
      border-radius: 4px;
      background: var(--hub);
    }}
    .task-nav__link.is-active {{
      background: var(--accent);
      color: var(--hub);
      font-weight: 600;
    }}
    .live-badge {{
      display: inline-block;
      background: #6b9e6b;
      color: #fff;
      font-size: 0.7rem;
      padding: 0.15rem 0.45rem;
      border-radius: 4px;
      margin-left: 0.5rem;
    }}
    .status-banner {{
      background: #4a3f35;
      border: 2px solid #d2c5b8;
      border-radius: 8px;
      padding: 0.75rem 1rem;
      margin-bottom: 1.25rem;
    }}
    .status-pill {{
      display: inline-block;
      font-size: 0.75rem;
      padding: 0.25rem 0.55rem;
      margin-right: 0.5rem;
      margin-bottom: 0.35rem;
      border-radius: 4px;
      background: #d2c5b8;
      color: #2a241c;
    }}
    .status-pill--ok {{ background: #8fbc8f; }}
    .status-pill--err {{ background: #e8b4b8; }}
    .status-banner__warn {{
      margin: 0.5rem 0 0;
      padding-left: 1.2rem;
      font-size: 0.82rem;
      color: #f5d9a8;
    }}
  </style>
</head>
<body>
  <h1>Transformations Toa AI</h1>
  {status_banner}
  {nav_html}
  <p class="meta">
    Tâche <code>{_html_escape(report.get("taskId", ""))}</code>
    · {_html_escape(report.get("sourceLanguage", "?"))}
    → {_html_escape(report.get("targetLanguage", "?"))}
    · {len(report.get("pages", []))} image(s)
    {'<span class="live-badge">Mise à jour en direct</span>' if auto_refresh else ''}
  </p>
  <div class="gallery">
    {''.join(pages_html) if pages_html else '<p class="empty">Aucune transformation enregistrée.</p>'}
  </div>
</body>
</html>"""


def _html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
