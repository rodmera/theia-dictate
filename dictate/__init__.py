"""TheIA Dictate — Módulo de captura de audio PipeWire y notas estructuradas."""

from dictate.audio import (
    CaptureSession,
    CapturedAudio,
    PipeWireCaptureSession,
    RecordingRequest,
    RingRecorder,
)
from dictate.notes import (
    StructuredNote,
    extract_structured_note,
    process_vault_note,
    render_note_markdown,
)

__all__ = [
    "CaptureSession",
    "CapturedAudio",
    "PipeWireCaptureSession",
    "RecordingRequest",
    "RingRecorder",
    "StructuredNote",
    "extract_structured_note",
    "process_vault_note",
    "render_note_markdown",
]
