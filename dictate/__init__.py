"""TheIA Dictate — Módulo de captura de audio PipeWire y notas estructuradas."""

from dictate.audio import (
    CaptureSession,
    CapturedAudio,
    PipeWireCaptureSession,
    RecordingRequest,
    RingRecorder,
    get_pipewire_default_devices,
)
from dictate.notes import (
    StructuredNote,
    extract_structured_note,
    process_vault_note,
    render_note_markdown,
)
from dictate.validator import (
    CapturedAudioValidator,
    EvidenceGate,
    PipeWireStreamProbe,
    USER_MESSAGES,
    ValidationFailure,
    ValidationResult,
    clean_manual_notes,
    is_blank_text,
)

__all__ = [
    "CaptureSession",
    "CapturedAudio",
    "PipeWireCaptureSession",
    "RecordingRequest",
    "RingRecorder",
    "get_pipewire_default_devices",
    "StructuredNote",
    "extract_structured_note",
    "process_vault_note",
    "render_note_markdown",
    "CapturedAudioValidator",
    "EvidenceGate",
    "PipeWireStreamProbe",
    "USER_MESSAGES",
    "ValidationFailure",
    "ValidationResult",
    "clean_manual_notes",
    "is_blank_text",
]
