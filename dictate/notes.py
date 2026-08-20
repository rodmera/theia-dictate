"""Módulo de refinamiento semántico y exportación determinista de notas a Obsidian.

Implementa un esquema estructurado estilo Granola para notas de voz y reuniones,
con renderizado Markdown predecible y entrega mediante el skill capture_note.
"""
from dataclasses import dataclass, field
from datetime import datetime
import os
import sys
from typing import Any, Callable

PROCESSED_RECORDINGS_SET: set[str] = set()


@dataclass
class StructuredNote:
    """Modelo estructurado para notas de voz y reuniones estilo Granola."""
    title: str
    created_at: datetime
    summary: str
    topic: str = "voice-note"
    key_points: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    action_items: list[dict[str, str]] = field(default_factory=list)
    raw_transcript: str = ""
    tags: list[str] = field(default_factory=lambda: ["unique", "voice-note"])


def render_note_markdown(note: StructuredNote) -> str:
    """Renderiza determinísticamente el cuerpo Markdown para la nota de Obsidian.

    El frontmatter YAML y el timestamp del título son gestionados canónicamente
    por capture_note(). El cuerpo renderizado contiene secciones estructuradas
    y libres de artefactos redundantes.
    """
    sections = []

    # 1. Resumen
    if note.summary:
        sections.append(f"## Resumen\n\n{note.summary.strip()}")

    # 2. Puntos clave
    if note.key_points:
        points_lines = "\n".join(f"- {p.strip()}" for p in note.key_points if p.strip())
        if points_lines:
            sections.append(f"## Puntos Clave\n\n{points_lines}")

    # 3. Decisiones
    if note.decisions:
        decisions_lines = "\n".join(f"- {d.strip()}" for d in note.decisions if d.strip())
        if decisions_lines:
            sections.append(f"## Decisiones\n\n{decisions_lines}")

    # 4. Compromisos y Tareas (Action Items)
    if note.action_items:
        action_lines = []
        for item in note.action_items:
            task = item.get("task", "").strip()
            if not task:
                continue
            owner = item.get("owner", "").strip()
            due = item.get("due", "").strip()
            meta_parts = []
            if owner:
                meta_parts.append(f"Responsable: {owner}")
            if due:
                meta_parts.append(f"Plazo: {due}")

            meta_str = f" ({' | '.join(meta_parts)})" if meta_parts else ""
            action_lines.append(f"- [ ] **{task}**{meta_str}")

        if action_lines:
            sections.append(f"## Compromisos y Tareas\n\n" + "\n".join(action_lines))

    # 5. Registro Textual / Transcripción
    if note.raw_transcript:
        transcript_clean = note.raw_transcript.strip()
        quoted = "\n".join(f"> {line}" for line in transcript_clean.splitlines())
        sections.append(f"## Registro Textual\n\n{quoted}")

    return "\n\n".join(sections).strip() + "\n"


def extract_structured_note(
    raw_text: str,
    res_json: dict[str, Any] | None = None,
    timestamp: datetime | None = None,
) -> StructuredNote:
    """Extrae una StructuredNote a partir del texto transcripto y el análisis LLM."""
    now = timestamp or datetime.now()
    res = res_json or {}

    title = res.get("note_title") or f"Nota de voz {now.strftime('%Y%m%d%H%M')}"
    summary = res.get("summary") or res.get("refined_text") or raw_text
    topic = res.get("topic") or "voice-note"

    key_points = res.get("key_points", [])
    if isinstance(key_points, str):
        key_points = [k.strip() for k in key_points.split("\n") if k.strip()]

    decisions = res.get("decisions", [])
    if isinstance(decisions, str):
        decisions = [d.strip() for d in decisions.split("\n") if d.strip()]

    action_items = res.get("action_items", [])
    if isinstance(action_items, list):
        parsed_actions = []
        for a in action_items:
            if isinstance(a, dict):
                parsed_actions.append(a)
            elif isinstance(a, str) and a.strip():
                parsed_actions.append({"task": a.strip()})
        action_items = parsed_actions
    else:
        action_items = []

    tags = res.get("tags") or ["unique", "voice-note"]
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    if "unique" not in tags:
        tags.insert(0, "unique")

    return StructuredNote(
        title=title,
        created_at=now,
        summary=summary,
        topic=topic,
        key_points=key_points,
        decisions=decisions,
        action_items=action_items,
        raw_transcript=raw_text,
        tags=tags,
    )


def process_vault_note(
    raw_text: str,
    res_json: dict[str, Any] | None = None,
    capture_fn: Callable[..., Any] | None = None,
    recording_id: str | None = None,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    """Renderiza determinísticamente la nota y la envía a capture_note.

    Garantiza idempotencia ante reintentos accidentales con el mismo recording_id.
    """
    if recording_id and recording_id in PROCESSED_RECORDINGS_SET:
        return {"status": "skipped", "reason": "already_processed", "recording_id": recording_id}

    note = extract_structured_note(raw_text, res_json=res_json, timestamp=timestamp)
    body_md = render_note_markdown(note)

    now = note.created_at
    ts = now.strftime("%Y%m%d%H%M")
    clean_title = note.title.strip()
    full_title = f"{ts} - {clean_title}" if not clean_title.startswith(ts) else clean_title

    fn = capture_fn
    if fn is None:
        sys.path.insert(0, os.path.expanduser("~/.openclaw/workspace/skills/obsidian-capture/scripts"))
        try:
            from capture import capture_note
            fn = capture_note
        except Exception as e:
            return {"status": "error", "error": f"No se pudo importar capture_note: {e}"}

    try:
        fm_extra = {"topic": note.topic} if note.topic else {}
        res = fn(full_title, body_md, tags=note.tags, frontmatter_extra=fm_extra, source="theia-notes")
        if isinstance(res, dict) and res.get("error"):
            return {"status": "error", "error": res["error"]}
        if recording_id:
            PROCESSED_RECORDINGS_SET.add(recording_id)
        return {
            "status": "ok",
            "title": full_title,
            "topic": note.topic,
            "tags": note.tags,
            "recording_id": recording_id,
            "path": res.get("path") if isinstance(res, dict) else None,
            "uri": res.get("uri") if isinstance(res, dict) else None,
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
