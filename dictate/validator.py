"""Módulo de validación estricta de señal acústica, estado de streams PipeWire y compuertas de evidencia.

Previene llamadas innecesarias o alucinaciones en LLMs cuando el audio capturado
es silencio, ruido blanco, o cuando no existe transcripción con evidencia textual.
"""
from dataclasses import dataclass
import math
import os
import re
import shutil
import struct
import subprocess
from typing import Any, Literal
import wave

SignalCode = Literal[
    "OK",
    "AUDIO_EMPTY",
    "AUDIO_DECODE_ERROR",
    "AUDIO_TOO_SHORT",
    "AUDIO_SILENT",
    "AUDIO_NOISE_ONLY",
    "PIPEWIRE_READER_STALLED",
    "PIPEWIRE_GRAPH_UNAVAILABLE",
    "SINK_NO_ACTIVE_STREAM",
    "TRANSCRIPT_EMPTY",
]

USER_MESSAGES: dict[SignalCode, str] = {
    "OK": "Señal válida.",
    "AUDIO_EMPTY": "El archivo de audio está vacío o no se generó.",
    "AUDIO_DECODE_ERROR": "El archivo de audio está dañado o tiene un formato no soportado.",
    "AUDIO_TOO_SHORT": "La grabación es demasiado corta para ser procesada (menos de 0.4s).",
    "AUDIO_SILENT": "No se detectó audio en la grabación (audio en silencio absoluto).",
    "AUDIO_NOISE_ONLY": "Solo se detectó ruido estático o de fondo sin señal de voz.",
    "PIPEWIRE_READER_STALLED": "El sistema de captura de audio PipeWire se detuvo inesperadamente.",
    "PIPEWIRE_GRAPH_UNAVAILABLE": "El servidor de audio PipeWire no está disponible en la sesión.",
    "SINK_NO_ACTIVE_STREAM": "El audio del PC estaba en silencio: ninguna aplicación estaba emitiendo sonido.",
    "TRANSCRIPT_EMPTY": "No se detectó voz ni transcripción válida para generar la minuta.",
}

# Líneas de plantilla por defecto en la GUI que no constituyen evidencia
DEFAULT_TEMPLATE_LINES = {
    "- acuerdo inicial:",
    "- tema discutido:",
    "- tarea pendiente:",
    "acuerdo inicial:",
    "tema discutido:",
    "tarea pendiente:",
}


@dataclass(frozen=True)
class ValidationFailure:
    """Detalle inmutable de un fallo de validación de señal o evidencia."""
    code: SignalCode
    user_message: str
    source: str = "mic"
    details: str = ""


@dataclass(frozen=True)
class ValidationResult:
    """Resultado de la comprobación de calidad de señal o evidencia."""
    valid: bool
    failure: ValidationFailure | None = None
    rms: float = 0.0
    duration_s: float = 0.0
    frames_count: int = 0


def is_blank_text(text: str | None) -> bool:
    """Retorna True si el texto es nulo, vacío o compuesto solo de puntuación y espacios."""
    if not text:
        return True
    stripped = text.strip()
    if not stripped:
        return True
    return all(ch in ".,;:!?¿¡-—()[]{}…\"'`~*#@&% \t\n\r" for ch in stripped)


def clean_manual_notes(manual_notes: str | None) -> str:
    """Elimina líneas de plantilla por defecto y espacios residuales."""
    if not manual_notes:
        return ""
    lines = manual_notes.splitlines()
    clean_lines = []
    for line in lines:
        l_str = line.strip().lower()
        if not l_str:
            continue
        # Si la línea es idéntica a una plantilla vacía, omitir
        if l_str in DEFAULT_TEMPLATE_LINES:
            continue
        clean_lines.append(line.strip())
    return "\n".join(clean_lines).strip()


_SILERO_MODEL = None


def get_silero_vad_model():
    """Carga o retorna la instancia en caché del modelo neuronal Silero VAD."""
    global _SILERO_MODEL
    if _SILERO_MODEL is not None:
        return _SILERO_MODEL
    try:
        from silero_vad import load_silero_vad
        _SILERO_MODEL = load_silero_vad()
        return _SILERO_MODEL
    except Exception:
        return None


class CapturedAudioValidator:
    """Validador determinista de señal acústica y detector neuronal de habla (Silero VAD)."""

    def __init__(
        self,
        min_duration_s: float = 0.35,
        min_rms: float = 0.003,
        min_peak_rms: float = 0.010,
        use_silero: bool = True,
        silero_threshold: float = 0.35,
    ):
        self.min_duration_s = min_duration_s
        self.min_rms = min_rms
        self.min_peak_rms = min_peak_rms
        self.use_silero = use_silero
        self.silero_threshold = silero_threshold

    def validate(self, audio_path: str, source: str = "mic") -> ValidationResult:
        res, _ = self.validate_and_trim(audio_path, source=source, trim=False)
        return res

    def validate_and_trim(
        self, audio_path: str, source: str = "mic", trim: bool = True, output_path: str | None = None
    ) -> tuple[ValidationResult, str]:
        if not audio_path or not os.path.exists(audio_path):
            return (
                ValidationResult(
                    valid=False,
                    failure=ValidationFailure(
                        code="AUDIO_EMPTY",
                        user_message=USER_MESSAGES["AUDIO_EMPTY"],
                        source=source,
                    ),
                ),
                audio_path,
            )

        file_size = os.path.getsize(audio_path)
        if file_size <= 44:  # Cabecera RIFF mínima
            return (
                ValidationResult(
                    valid=False,
                    failure=ValidationFailure(
                        code="AUDIO_EMPTY",
                        user_message=USER_MESSAGES["AUDIO_EMPTY"],
                        source=source,
                        details=f"Tamaño de archivo insuficiente: {file_size} bytes",
                    ),
                ),
                audio_path,
            )

        try:
            with wave.open(audio_path, "rb") as w:
                n_channels = w.getnchannels()
                sample_width = w.getsampwidth()
                framerate = w.getframerate()
                n_frames = w.getnframes()
                raw_frames = w.readframes(n_frames)
        except Exception as err:
            return (
                ValidationResult(
                    valid=False,
                    failure=ValidationFailure(
                        code="AUDIO_DECODE_ERROR",
                        user_message=USER_MESSAGES["AUDIO_DECODE_ERROR"],
                        source=source,
                        details=str(err),
                    ),
                ),
                audio_path,
            )

        if n_channels < 1 or sample_width != 2 or framerate < 8000:
            return (
                ValidationResult(
                    valid=False,
                    failure=ValidationFailure(
                        code="AUDIO_DECODE_ERROR",
                        user_message=USER_MESSAGES["AUDIO_DECODE_ERROR"],
                        source=source,
                        details=f"Formato no válido: ch={n_channels}, width={sample_width}, rate={framerate}",
                    ),
                ),
                audio_path,
            )

        duration_s = round(n_frames / framerate, 3)
        if duration_s < self.min_duration_s:
            return (
                ValidationResult(
                    valid=False,
                    failure=ValidationFailure(
                        code="AUDIO_TOO_SHORT",
                        user_message=USER_MESSAGES["AUDIO_TOO_SHORT"],
                        source=source,
                        details=f"Duración {duration_s}s < mín {self.min_duration_s}s",
                    ),
                    duration_s=duration_s,
                    frames_count=n_frames,
                ),
                audio_path,
            )

        # 1. Detección neuronal de habla y recorte de silencio (Silero VAD - SOTA de la industria)
        if self.use_silero and framerate in (8000, 16000):
            silero_model = get_silero_vad_model()
            if silero_model is not None:
                try:
                    import torch
                    import numpy as np
                    from silero_vad import get_speech_timestamps, collect_chunks

                    samples_np = np.frombuffer(raw_frames, dtype=np.int16).astype(np.float32) / 32768.0
                    tensor = torch.from_numpy(samples_np)
                    timestamps = get_speech_timestamps(
                        tensor,
                        silero_model,
                        sampling_rate=framerate,
                        threshold=self.silero_threshold,
                        min_speech_duration_ms=250,
                        speech_pad_ms=300,
                    )
                    if not timestamps:
                        return (
                            ValidationResult(
                                valid=False,
                                failure=ValidationFailure(
                                    code="AUDIO_SILENT",
                                    user_message=USER_MESSAGES["AUDIO_SILENT"],
                                    source=source,
                                    details="Silero VAD: no se detectaron fonemas de habla humana en la señal",
                                ),
                                duration_s=duration_s,
                                frames_count=n_frames,
                            ),
                            audio_path,
                        )

                    target_path = output_path or audio_path
                    if trim and timestamps:
                        trimmed_tensor = collect_chunks(timestamps, tensor)
                        trimmed_int16 = (trimmed_tensor.numpy() * 32767.0).astype(np.int16)
                        trimmed_duration = round(len(trimmed_int16) / framerate, 3)
                        with wave.open(target_path, "wb") as w_out:
                            w_out.setnchannels(n_channels)
                            w_out.setsampwidth(sample_width)
                            w_out.setframerate(framerate)
                            w_out.writeframes(trimmed_int16.tobytes())
                        return (
                            ValidationResult(
                                valid=True,
                                duration_s=trimmed_duration,
                                frames_count=len(trimmed_int16),
                            ),
                            target_path,
                        )
                    else:
                        return (
                            ValidationResult(
                                valid=True,
                                duration_s=duration_s,
                                frames_count=n_frames,
                            ),
                            target_path,
                        )
                except Exception:
                    # Fallback acústico determinista si falla PyTorch/Silero
                    pass

        # 2. Fallback acústico determinista (análisis de dinámica y picos RMS)
        num_samples = len(raw_frames) // 2
        if num_samples == 0:
            return (
                ValidationResult(
                    valid=False,
                    failure=ValidationFailure(
                        code="AUDIO_EMPTY",
                        user_message=USER_MESSAGES["AUDIO_EMPTY"],
                        source=source,
                    ),
                ),
                audio_path,
            )

        samples = struct.unpack("<%dh" % num_samples, raw_frames[:num_samples * 2])
        sum_sq = sum((s / 32768.0) ** 2 for s in samples)
        rms = math.sqrt(sum_sq / num_samples)

        chunk_size = 512  # ~32ms @ 16kHz
        chunk_rms_list = []
        for i in range(0, len(samples), chunk_size):
            chunk = samples[i:i + chunk_size]
            if len(chunk) < 64:
                continue
            c_rms = math.sqrt(sum((s / 32768.0) ** 2 for s in chunk) / len(chunk))
            chunk_rms_list.append(c_rms)

        max_chunk_rms = max(chunk_rms_list) if chunk_rms_list else rms

        # Se considera silencio solo si el RMS global es bajo Y no hubo picos de voz
        if rms < self.min_rms and max_chunk_rms < self.min_peak_rms:
            return (
                ValidationResult(
                    valid=False,
                    failure=ValidationFailure(
                        code="AUDIO_SILENT",
                        user_message=USER_MESSAGES["AUDIO_SILENT"],
                        source=source,
                        details=f"RMS {rms:.5f} < umbral {self.min_rms} (pico {max_chunk_rms:.5f} < {self.min_peak_rms})",
                    ),
                    rms=rms,
                    duration_s=duration_s,
                    frames_count=n_frames,
                ),
                audio_path,
            )

        if chunk_rms_list:
            mean_c = sum(chunk_rms_list) / len(chunk_rms_list)
            var_c = sum((c - mean_c) ** 2 for c in chunk_rms_list) / len(chunk_rms_list)
            std_c = math.sqrt(var_c)

            # Si es un tono/zumbido 100% plano sin variabilidad de habla y RMS bajo
            if std_c < 0.00015 and rms < 0.008:
                return (
                    ValidationResult(
                        valid=False,
                        failure=ValidationFailure(
                            code="AUDIO_NOISE_ONLY",
                            user_message=USER_MESSAGES["AUDIO_NOISE_ONLY"],
                            source=source,
                            details=f"Desviación RMS insuficiente ({std_c:.6f}), señal estática",
                        ),
                        rms=rms,
                        duration_s=duration_s,
                        frames_count=n_frames,
                    ),
                    audio_path,
                )

        return (
            ValidationResult(
                valid=True,
                rms=rms,
                duration_s=duration_s,
                frames_count=n_frames,
            ),
            audio_path,
        )


class PipeWireStreamProbe:
    """Verifica el estado de los streams y sinks de PipeWire."""

    @staticmethod
    def check_sink_active_streams() -> bool:
        """Retorna True si hay streams de reproducción activos en PipeWire."""
        if not shutil.which("wpctl"):
            return True  # Asumir disponible si no está wpctl

        try:
            out = subprocess.run(["wpctl", "status"], capture_output=True, text=True, timeout=2).stdout
            in_streams = False
            for line in out.splitlines():
                if "Streams:" in line:
                    in_streams = True
                    continue
                if in_streams:
                    if line and not line.startswith(" ") and not line.startswith("│"):
                        break
                    if "[active]" in line or "output_" in line or ">" in line:
                        return True
            return False
        except Exception:
            return True


class EvidenceGate:
    """Compuerta de evidencia textual para prevenir minutas alucinadas."""

    @staticmethod
    def check_evidence(raw_text: str, manual_notes: str = "") -> ValidationResult:
        """Verifica que exista transcripción válida antes de permitir el refinamiento LLM."""
        if is_blank_text(raw_text):
            return ValidationResult(
                valid=False,
                failure=ValidationFailure(
                    code="TRANSCRIPT_EMPTY",
                    user_message=USER_MESSAGES["TRANSCRIPT_EMPTY"],
                    details="Transcripción vacía o compuesta solo de ruido/puntuación",
                ),
            )

        return ValidationResult(valid=True)
