"""Módulo de captura de audio nativo basado en PipeWire para TheIA Dictate.

Proporciona una interfaz unificada CaptureSession respaldada por pw-record, con
stream continuo en background y buffer circular de pre-roll (1.0s) para eliminar
la latencia de inicio y evitar la pérdida de la primera sílaba.
"""
from collections import deque
from dataclasses import dataclass
import os
import shutil
import struct
import subprocess
import threading
import time
from typing import Any, Callable, Protocol, runtime_checkable
import uuid


@dataclass(frozen=True)
class RecordingRequest:
    """Solicitud de inicio de grabación."""
    mode: str = "default"
    language: str = "es"
    source: str = "mic"  # "mic" | "monitor" | "mixed"
    auto_stop: str | None = None  # "vad" | "silence" | None


@dataclass(frozen=True)
class CapturedAudio:
    """Resultado inmutable de una sesión de audio capturada."""
    path: str
    sample_rate: int = 16000
    channels: int = 1
    sample_width: int = 2
    duration_s: float = 0.0
    recording_id: str = ""
    source: str = "mic"


@runtime_checkable
class CaptureSession(Protocol):
    """Protocolo de sesión de captura para el runtime de TheIA Dictate."""
    def start(self) -> None: ...
    def begin_recording(self, request: RecordingRequest | None = None) -> None: ...
    def stop_recording(self, output_path: str = "/tmp/theia-dictate-audio.wav") -> CapturedAudio | None: ...
    def cancel_recording(self) -> None: ...
    def close(self) -> None: ...


def write_wav(path: str, raw_pcm: bytes, sample_rate: int = 16000, channels: int = 1, sample_width: int = 2) -> bool:
    """Escribe un archivo WAV canónico con cabecera RIFF a partir de bytes PCM S16_LE."""
    if not raw_pcm:
        return False
    data_size = len(raw_pcm)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_size, b"WAVE",
        b"fmt ", 16, 1, channels, sample_rate,
        sample_rate * channels * sample_width, channels * sample_width, 16,
        b"data", data_size,
    )
    try:
        with open(path, "wb") as f:
            f.write(header)
            f.write(raw_pcm)
        return True
    except Exception:
        return False


class PipeWireCaptureSession:
    """Grabador continuo respaldado por PipeWire (pw-record) con pre-roll circular de 1.0s.

    Mantiene un stream de captura abierto en background para latencia de inicio 0ms
    y preservación garantizada del onset de habla.
    """

    def __init__(
        self,
        pre_roll_ms: int = 1000,
        sample_rate: int = 16000,
        channels: int = 1,
        sample_width: int = 2,
        target_node: str | None = None,
    ):
        self.sample_rate = sample_rate
        self.channels = channels
        self.sample_width = sample_width
        self.target_node = target_node

        self.frame_ms = 32
        self.frame_samples = self.sample_rate * self.frame_ms // 1000  # 512 samples @ 16kHz
        self.frame_bytes = self.frame_samples * self.channels * self.sample_width  # 1024 bytes
        self.pre_roll_frames = max(1, pre_roll_ms // self.frame_ms)
        self.pre_roll_buffer: deque = deque(maxlen=self.pre_roll_frames)

        self.active = False
        self.current_request: RecordingRequest | None = None
        self.recording_id: str = ""
        self.recorded_bytes = bytearray()
        self.lock = threading.Lock()

        self.proc: subprocess.Popen | None = None
        self.thread: threading.Thread | None = None
        self.running = False
        self.on_vad_stop_callback: Callable[[], None] | None = None
        self.vad_instance: Any = None

    def start(self) -> None:
        """Inicia el lector PipeWire en background."""
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._reader_loop, daemon=True, name="PipeWireCaptureReader")
        self.thread.start()

    def close(self) -> None:
        """Detiene el lector y finaliza el proceso de captura."""
        self.running = False
        self._kill_proc()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1.0)

    def stop(self) -> None:
        """Alias para close() compatible con RingRecorder histórico."""
        self.close()

    def _kill_proc(self) -> None:
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=0.5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None

    def _build_capture_cmd(self, source: str = "mic") -> list[str]:
        """Construye el comando de captura con prioridad absoluta a PipeWire (pw-record)."""
        if shutil.which("pw-record"):
            cmd = [
                "pw-record",
                "--container", "raw",
                "--format", "s16",
                "--rate", str(self.sample_rate),
                "--channels", str(self.channels),
            ]
            if self.target_node:
                cmd.extend(["--target", str(self.target_node)])
            cmd.append("-")
            return cmd
        # Fallback a arecord si pw-record no estuviese instalado
        return [
            "arecord", "-D", "default", "-f", "S16_LE",
            "-c", str(self.channels), "-r", str(self.sample_rate), "-t", "raw"
        ]

    def _reader_loop(self) -> None:
        """Loop continuo que lee el stream de audio y alimenta el pre-roll o la grabación activa."""
        while self.running:
            cmd = self._build_capture_cmd()
            try:
                self.proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    bufsize=self.frame_bytes * 10
                )
            except Exception:
                time.sleep(1.0)
                continue

            try:
                while self.running and self.proc and self.proc.poll() is None:
                    chunk = self.proc.stdout.read(self.frame_bytes) if self.proc.stdout else b""
                    if not chunk:
                        break
                    while len(chunk) < self.frame_bytes and self.proc and self.proc.stdout:
                        more = self.proc.stdout.read(self.frame_bytes - len(chunk))
                        if not more:
                            break
                        chunk += more
                    if len(chunk) < self.frame_bytes:
                        break

                    ints = struct.unpack("<%dh" % (self.frame_bytes // self.sample_width), chunk)

                    vad_stop = False
                    with self.lock:
                        if not self.active:
                            self.pre_roll_buffer.append((ints, chunk))
                        else:
                            self.recorded_bytes.extend(chunk)
                            if self.vad_instance:
                                done, _ = self.vad_instance.push(list(ints), chunk)
                                if done:
                                    vad_stop = True

                    if vad_stop and self.on_vad_stop_callback:
                        self.on_vad_stop_callback()

            except Exception:
                pass
            finally:
                self._kill_proc()
                if self.running:
                    time.sleep(0.2)

    def feed_frame(self, frame_ints: Any, raw_bytes: bytes) -> None:
        """Inyección directa de frames (usado en tests y simulación de hardware)."""
        vad_stop = False
        with self.lock:
            if not self.active:
                self.pre_roll_buffer.append((frame_ints, raw_bytes))
            else:
                self.recorded_bytes.extend(raw_bytes)
                if self.vad_instance:
                    done, _ = self.vad_instance.push(list(frame_ints), raw_bytes)
                    if done:
                        vad_stop = True

        if vad_stop and self.on_vad_stop_callback:
            self.on_vad_stop_callback()

    def begin_recording(
        self,
        request: RecordingRequest | None = None,
        vad_instance: Any = None,
        on_vad_stop: Callable[[], None] | None = None,
    ) -> None:
        """Inicia una sesión de grabación incorporando el buffer de pre-roll."""
        with self.lock:
            self.active = True
            self.current_request = request or RecordingRequest()
            self.recording_id = uuid.uuid4().hex[:8]
            self.recorded_bytes = bytearray()
            self.vad_instance = vad_instance
            if on_vad_stop is not None:
                self.on_vad_stop_callback = on_vad_stop

            # Drenar pre-roll acumulado antes de la pulsación
            for ints, chunk in self.pre_roll_buffer:
                self.recorded_bytes.extend(chunk)
                if self.vad_instance:
                    self.vad_instance.push(list(ints), chunk)
            self.pre_roll_buffer.clear()

    def stop_recording(self, output_path: str = "/tmp/theia-dictate-audio.wav") -> CapturedAudio | None:
        """Finaliza la grabación activa y escribe el archivo WAV final."""
        with self.lock:
            if not self.active:
                return None
            self.active = False
            self.vad_instance = None
            raw_pcm = bytes(self.recorded_bytes)
            self.recorded_bytes = bytearray()
            rec_id = self.recording_id
            src = self.current_request.source if self.current_request else "mic"

        if not raw_pcm:
            return None

        written = write_wav(output_path, raw_pcm, self.sample_rate, self.channels, self.sample_width)
        if not written:
            return None

        duration_s = round(len(raw_pcm) / (self.sample_rate * self.channels * self.sample_width), 2)
        return CapturedAudio(
            path=output_path,
            sample_rate=self.sample_rate,
            channels=self.channels,
            sample_width=self.sample_width,
            duration_s=duration_s,
            recording_id=rec_id,
            source=src,
        )

    def cancel_recording(self) -> None:
        """Cancela la grabación activa descartando el audio acumulado."""
        with self.lock:
            self.active = False
            self.vad_instance = None
            self.recorded_bytes = bytearray()
            self.recording_id = ""

    def end_recording(self, output_wav_path: str = "/tmp/theia-dictate-audio.wav", output_path: str | None = None) -> bytes:
        """Compatibilidad histórica con RingRecorder.end_recording(). Retorna los bytes PCM."""
        target_path = output_path or output_wav_path
        with self.lock:
            if not self.active:
                return b""
            self.active = False
            self.vad_instance = None
            self.on_vad_stop_callback = None
            raw_pcm = bytes(self.recorded_bytes)
            self.recorded_bytes = bytearray()

        if raw_pcm:
            write_wav(target_path, raw_pcm, sample_rate=self.sample_rate, channels=self.channels, sample_width=self.sample_width)
            return raw_pcm
        return b""


# Alias histórico
RingRecorder = PipeWireCaptureSession
