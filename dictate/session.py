"""Gestor de sesiones de grabación y procesamiento de notas de reunión para TheIA Notes.

Coordina la captura PipeWire (micrófono / salida PC / mezcla de reunión), la
integración de apuntes manuales en vivo y el refinamiento con Gemini 3.7 Flash.
"""
from dataclasses import dataclass
from datetime import datetime
import os
import shutil
import tempfile
import threading
from typing import Any, Callable

from dictate.audio import (
    CapturedAudio,
    PipeWireCaptureSession,
    RecordingRequest,
    get_pipewire_default_devices,
)
from dictate.notes import (
    StructuredNote,
    extract_structured_note,
    process_vault_note,
    render_note_markdown,
)


@dataclass
class SessionState:
    status: str = "idle"  # "idle" | "recording" | "processing" | "ready" | "error"
    session_id: str = ""
    source: str = "meeting"  # "meeting" | "monitor" | "mic"
    language: str = "es"
    duration_s: float = 0.0
    manual_notes: str = ""
    audio_path: str = ""
    error_message: str = ""
    note: StructuredNote | None = None


class NotesSessionManager:
    """Coordinador de sesión para TheIA Notes (headless y desacoplado de la GUI)."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.state = SessionState()
        self.session_dir: str | None = None
        self.capture_session: PipeWireCaptureSession | None = None
        self.lock = threading.Lock()

    def get_devices_info(self) -> dict[str, str]:
        """Obtiene el resumen de dispositivos predeterminados de PipeWire."""
        return get_pipewire_default_devices()

    def start_recording(self, source: str = "meeting", language: str = "es") -> bool:
        """Inicia la captura de audio en un directorio temporal seguro (0700)."""
        with self.lock:
            if self.state.status == "recording":
                return False

            self.session_dir = tempfile.mkdtemp(prefix="theia-notes-")
            os.chmod(self.session_dir, 0o700)

            audio_file = os.path.join(self.session_dir, "meeting-audio.wav")
            self.capture_session = PipeWireCaptureSession(pre_roll_ms=1000, target_source=source)
            self.capture_session.start()

            req = RecordingRequest(mode="vault", language=language, source=source)
            self.capture_session.begin_recording(req)

            self.state = SessionState(
                status="recording",
                session_id=self.capture_session.recording_id,
                source=source,
                language=language,
                audio_path=audio_file,
                manual_notes="",
            )
            return True

    def stop_and_process(
        self,
        manual_notes: str = "",
        transcribe_fn: Callable[..., dict[str, Any]] | None = None,
        llm_fn: Callable[..., dict[str, Any]] | None = None,
        on_complete: Callable[[StructuredNote], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        """Detiene la grabación y procesa la nota con IA de forma asíncrona."""
        with self.lock:
            if self.state.status != "recording" or not self.capture_session:
                if on_error:
                    on_error("No hay una sesión de grabación activa")
                return

            self.state.status = "processing"
            self.state.manual_notes = manual_notes
            captured: CapturedAudio | None = self.capture_session.stop_recording(self.state.audio_path)
            self.capture_session.close()
            self.capture_session = None

            if captured:
                self.state.duration_s = captured.duration_s

        threading.Thread(
            target=self._process_worker,
            args=(manual_notes, transcribe_fn, llm_fn, on_complete, on_error),
            daemon=True,
            name="NotesProcessorWorker",
        ).start()

    def _process_worker(
        self,
        manual_notes: str,
        transcribe_fn: Callable[..., dict[str, Any]] | None,
        llm_fn: Callable[..., dict[str, Any]] | None,
        on_complete: Callable[[StructuredNote], None] | None,
        on_error: Callable[[str], None] | None,
    ) -> None:
        try:
            # 1. Transcripción
            t_fn = transcribe_fn
            if t_fn is None:
                # Import dinámico del transcriptor
                from theia_dictate_module import transcribe_audio
                t_fn = lambda path, lang: transcribe_audio(path, language=lang, config=self.config)

            res_transcribe = t_fn(self.state.audio_path, self.state.language)
            raw_text = res_transcribe.get("text", "") if isinstance(res_transcribe, dict) else str(res_transcribe)
            if not raw_text or res_transcribe.get("error"):
                err = res_transcribe.get("error", "No se detectó audio en la llamada")
                self.state.status = "error"
                self.state.error_message = err
                if on_error:
                    on_error(err)
                return

            # 2. Refinamiento semántico estructurado
            structured_res = None
            if llm_fn:
                structured_res = llm_fn(raw_text, manual_notes)
            else:
                structured_res = self._call_gemini_meeting_refinement(raw_text, manual_notes)

            note = extract_structured_note(raw_text, res_json=structured_res)
            with self.lock:
                self.state.status = "ready"
                self.state.note = note

            if on_complete:
                on_complete(note)

        except Exception as exc:
            with self.lock:
                self.state.status = "error"
                self.state.error_message = str(exc)
            if on_error:
                on_error(str(exc))

    def _call_gemini_meeting_refinement(self, raw_text: str, manual_notes: str) -> dict[str, Any]:
        """Envía la transcripción y notas manuales a Gemini 3.7 Flash para estructuración Granola."""
        try:
            from shared_config import call_gemini_structured
        except Exception:
            # Fallback simple
            return {
                "note_title": f"Reunión {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                "summary": manual_notes or raw_text[:200],
                "key_points": [line.strip("- ") for line in (manual_notes or raw_text).splitlines() if line.strip()][:5],
                "decisions": [],
                "action_items": [],
                "tags": ["unique", "voice-note", "reunion"],
            }

        schema = {
            "type": "OBJECT",
            "properties": {
                "note_title": {
                    "type": "STRING",
                    "description": "Título conciso, profesional y descriptivo para la nota de la reunión (máx 60 caracteres)."
                },
                "summary": {
                    "type": "STRING",
                    "description": "Resumen ejecutivo de alto nivel de lo tratado en la reunión."
                },
                "topic": {
                    "type": "STRING",
                    "description": "Tema o categoría principal de la nota (ej: reunión, proyecto, cliente)."
                },
                "key_points": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                    "description": "Puntos clave y argumentos discutidos durante la llamada."
                },
                "decisions": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                    "description": "Decisiones concretas o acuerdos alcanzados."
                },
                "action_items": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "task": {"type": "STRING"},
                            "owner": {"type": "STRING"},
                            "due": {"type": "STRING"}
                        },
                        "required": ["task"]
                    },
                    "description": "Compromisos, acuerdos y tareas pendientes con responsable y plazo si se mencionó."
                },
                "tags": {
                    "type": "ARRAY",
                    "items": {"type": "STRING"},
                    "description": "Tags relevantes para Obsidian (incluir 'unique', 'voice-note', 'reunion')."
                }
            },
            "required": ["note_title", "summary"]
        }

        notes_context = f"\nAPUNTES MANUALES DEL USUARIO (Prioridad contextual alta):\n{manual_notes}\n" if manual_notes else ""
        prompt = (
            "Eres el asistente de reuniones ejecutivas TheIA Notes. Analiza la siguiente transcripción de reunión "
            "junto a los apuntes manuales tomados en vivo por el usuario. Genera una minuta ejecutiva estilo Granola "
            "con resumen conciso, puntos clave, decisiones y lista de tareas/compromisos con responsables claros.\n\n"
            f"{notes_context}"
            f"TRANSCRIPCIÓN COMPLETA DE LA LLAMADA:\n{raw_text}"
        )

        res = call_gemini_structured(prompt, schema, model="gemini-3.7-flash", temperature=0.1, timeout=60)
        return res or {}

    def save_note_to_vault(self, note: StructuredNote | None = None, capture_fn: Any = None) -> dict[str, Any]:
        """Guarda la nota en Obsidian Vault mediante el skill capture_note y limpia la sesión."""
        target_note = note or self.state.note
        if not target_note:
            return {"status": "error", "error": "No hay una nota estructurada para guardar"}

        res = process_vault_note(
            raw_text=target_note.raw_transcript,
            res_json={
                "note_title": target_note.title,
                "summary": target_note.summary,
                "topic": target_note.topic,
                "key_points": target_note.key_points,
                "decisions": target_note.decisions,
                "action_items": target_note.action_items,
                "tags": target_note.tags,
            },
            capture_fn=capture_fn,
            recording_id=self.state.session_id,
            timestamp=target_note.created_at,
        )

        if res.get("status") == "ok":
            self.cleanup_session()
        return res

    def cancel_session(self) -> None:
        """Cancela la sesión activa y elimina inmediatamente los archivos de audio."""
        with self.lock:
            if self.capture_session:
                self.capture_session.cancel_recording()
                self.capture_session.close()
                self.capture_session = None
            self.cleanup_session()
            self.state = SessionState(status="idle")

    def cleanup_session(self) -> None:
        """Limpia el directorio temporal de audio."""
        if self.session_dir and os.path.exists(self.session_dir):
            try:
                shutil.rmtree(self.session_dir)
            except Exception:
                pass
            self.session_dir = None
