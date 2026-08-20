#!/usr/bin/env python3
"""VAD con suavizado (onset/hangover/prefill) para TheIA Dictate.

Reemplaza el auto-stop por silencio simple con detección de voz más robusta,
imitando el `SmoothedVad` de Handy: no corta por un micro-silencio (hangover) ni
arranca por un ruidito (onset), y conserva el audio que precede al inicio de la
voz (prefill).

Backends de detección:
  - silero-vad (si `silero_vad` está instalado): red LSTM de Silero, robusta al ruido.
  - energía RMS (fallback): umbral sobre la señal, suficiente para voz limpia.

La captura se hace leyendo `arecord` en modo raw (S16_LE, mono, 16 kHz) por frames,
así no se necesita sounddevice. El audio capturado se escribe como WAV válido al terminar.
"""

import argparse
import json
import math
import os
import shutil
import signal
import struct
import subprocess
import sys
import time

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # S16_LE
FRAME_MS = 32          # tamaño de frame en ms (512 muestras, exigido por silero-vad >= 6.x)
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 512

AUDIO_FILE = "/tmp/theia-dictate-audio.wav"
STATUS_FILE = "/tmp/theia-dictate-vad-status.json"


def _rms(ints):
    if not ints:
        return 0.0
    return math.sqrt(sum(s * s for s in ints) / len(ints))


class EnergyVad:
    """Detección de voz por energía RMS por frame. Sin dependencias externas."""

    def __init__(self, threshold=0.02):
        self.threshold = threshold

    def frame_is_speech(self, frame):
        return _rms(frame) > self.threshold

    def calibrate(self, frames):
        """Ajusta el umbral al piso de ruido real del mic.

        Usa el MÍNIMO de RMS de los frames de calibración como piso de ruido:
        la mediana se contamina si el usuario ya está hablando durante la
        calibración (el umbral quedaría en nivel de voz y nada se detecta).
        El mínimo es robusto: basta un frame de silencio/ruido ambiente
        (típico entre palabras o al arrancar) para fijar un umbral sano.
        threshold = max(min_rms * 1.3, 0.02).
        """
        if frames:
            floors = sorted(_rms(f) for f in frames)
            noise = floors[0]
            self.threshold = max(noise * 1.3, 0.02)


class SileroVadBackend:
    """silero-vad si está disponible; fallback a energía si no."""

    def __init__(self, threshold=0.3):
        # 0.5 es el default de silero (conservador); 0.3 detecta voz más suave
        # / a distancia (mic de webcam), clave para dictado con mic USB lejano.
        self.threshold = threshold
        self.silero = None
        try:
            from silero_vad import load_silero_vad
            self.silero = load_silero_vad()
        except Exception:
            self.silero = None

    def frame_is_speech(self, frame_int16):
        if self.silero is not None:
            try:
                import numpy as np
                x = np.array(frame_int16, dtype=np.float32) / 32768.0
                if hasattr(self.silero, "predict_chunk"):
                    # silero-vad <= 4.x
                    return bool(self.silero.predict_chunk(x) > self.threshold)
                # silero-vad >= 6.x: forward(tensor, sr)
                import torch
                return bool(self.silero(torch.from_numpy(x), SAMPLE_RATE).item() > self.threshold)
            except Exception:
                pass
        return _rms(frame_int16) > 0.012


class SmoothedVad:
    """VAD para auto-stop por silencio que NUNCA recorta la cabeza del audio.

    Graba TODO desde el primer frame (t=0), como WhisperBox/Voxtype: el detector
    solo decide CUÁNDO cortar (pausa de silencio), no qué audio se conserva.
    Esto elimina la pérdida de la primera palabra: no hay onset que descarte
    los frames iniciales ni prefill que no alcance a cubrir el arranque.

    Máquina de estados:
      - `recorded` acumula cada frame desde el inicio (siempre).
      - Antes de entrar en habla: se requieren `onset_frames` consecutivos de voz
        para marcar `speech_detected` (sirve para descartar grabaciones que no
        tienen voz, no para recortar audio).
      - En habla: el hangover mantiene el corte a la espera N frames tras la
        última señal de voz; al agotarse (por un silencio), si la pausa llega a
        `max_idle_frames` sin reanudar, se corta la grabación.
      - `finalize()` (soltar tecla PTT / timeout) devuelve TODO lo grabado si
        hubo voz.
    """

    def __init__(self, detector, onset_ms=120, hangover_ms=500, prefill_ms=1000,
                 max_silence_ms=1500):
        self.detector = detector
        self.frame_ms = FRAME_MS
        self.onset_frames = max(1, onset_ms // self.frame_ms)
        self.hangover_frames = max(0, hangover_ms // self.frame_ms)
        # prefill ya no se usa para el corte (grabamos desde t=0); se mantiene
        # el parámetro por compatibilidad de firma.
        self.max_idle_frames = max(1, max_silence_ms // self.frame_ms)

        self.recorded = bytearray()  # TODO el audio capturado desde t=0
        self.onset_counter = 0
        self.hangover_frames_left = 0
        self.idle_frames = 0
        self.in_speech = False
        self.speech_detected = False

    def push(self, frame_int16, raw_bytes):
        """Feed de un frame. Retorna (done, final_audio_bytes_or_None)."""
        is_speech = self.detector.frame_is_speech(frame_int16)

        # Grabar SIEMPRE desde t=0: nunca recortar la cabeza del audio.
        self.recorded.extend(raw_bytes)

        if not self.in_speech:
            if is_speech:
                self.onset_counter += 1
                if self.onset_counter >= self.onset_frames:
                    self.in_speech = True
                    self.speech_detected = True
                    self.hangover_frames_left = self.hangover_frames
                    self.idle_frames = 0
            else:
                self.onset_counter = 0
            return False, None

        # En habla: actualizar ventana de corte
        if is_speech:
            self.hangover_frames_left = self.hangover_frames
            self.idle_frames = 0
        else:
            self.hangover_frames_left -= 1
            self.idle_frames += 1

        # Cortar solo si: hangover agotado Y la pausa ya es larga (max_idle_frames)
        if self.hangover_frames_left <= 0 and self.idle_frames >= self.max_idle_frames:
            self.in_speech = False
            return True, bytes(self.recorded)

        return False, None

    def finalize(self):
        """Llamada al agotar tiempo de grabación o por señal (soltar tecla PTT)."""
        self.in_speech = False
        return bytes(self.recorded) if self.speech_detected else None


def _build_capture_cmd():
    if shutil.which("pw-record"):
        return [
            "pw-record",
            "--container", "raw",
            "--format", "s16",
            "--rate", str(SAMPLE_RATE),
            "--channels", str(CHANNELS),
            "-"
        ]
    return [
        "arecord", "-D", "default", "-f", "S16_LE",
        "-c", str(CHANNELS), "-r", str(SAMPLE_RATE), "-t", "raw"
    ]


def _iter_pipewire_frames(cmd=None):
    if cmd is None:
        cmd = _build_capture_cmd()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    frame_bytes = FRAME_SAMPLES * CHANNELS * SAMPLE_WIDTH
    try:
        while True:
            chunk = proc.stdout.read(frame_bytes) if proc.stdout else b""
            if not chunk:
                break
            if len(chunk) < frame_bytes and proc.stdout:
                chunk += proc.stdout.read(frame_bytes - len(chunk))
                if len(chunk) < frame_bytes:
                    break
            ints = struct.unpack("<%dh" % (frame_bytes // SAMPLE_WIDTH), chunk[:frame_bytes])
            yield list(ints), chunk[:frame_bytes]
    finally:
        try:
            if proc.stdout:
                proc.stdout.close()
        except Exception:
            pass
        try:
            proc.terminate()
        except Exception:
            pass


_iter_arecord_frames = _iter_pipewire_frames


def _iter_wav_frames(path):
    """Lee frames (ints, raw) desde un WAV mono/16kHz/S16_LE en vez del micrófono.

    Usado por `--test-file` para probar el flujo VAD completo sin arecord.
    """
    import wave
    with wave.open(path, "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2 or w.getframerate() != SAMPLE_RATE:
            raise ValueError(
                f"WAV de test debe ser mono/16kHz/S16_LE (ch={w.getnchannels()} "
                f"sr={w.getframerate()} sw={w.getsampwidth()})"
            )
        frame_bytes = FRAME_SAMPLES * CHANNELS * SAMPLE_WIDTH
        while True:
            chunk = w.readframes(FRAME_SAMPLES)
            if not chunk:
                break
            if len(chunk) < frame_bytes:
                chunk += b"\x00" * (frame_bytes - len(chunk))
            ints = struct.unpack("<%dh" % (frame_bytes // SAMPLE_WIDTH), chunk[:frame_bytes])
            yield list(ints), chunk[:frame_bytes]


def _write_wav(path, raw_pcm):
    if not raw_pcm:
        return False
    data_size = len(raw_pcm)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_size, b"WAVE",
        b"fmt ", 16, 1, CHANNELS, SAMPLE_RATE,
        SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH, CHANNELS * SAMPLE_WIDTH, 16,
        b"data", data_size,
    )
    with open(path, "wb") as f:
        f.write(header)
        f.write(raw_pcm)
    return True


STOP_REQUESTED = False


def _on_signal(signum, frame):
    """SIGINT/SIGTERM: soltar tecla PTT o detención del daemon -> finalizar ya."""
    global STOP_REQUESTED
    STOP_REQUESTED = True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--onset-ms", type=int, default=120)
    ap.add_argument("--hangover-ms", type=int, default=500)
    ap.add_argument("--prefill-ms", type=int, default=1000)
    ap.add_argument("--max-silence-ms", type=int, default=1500,
                    help="Pausa sin voz (post-hangover) que dispara el corte automático.")
    ap.add_argument("--use-silero", action="store_true", default=True,
                    help="Usar silero-vad (por defecto). --no-silero fuerza energía.")
    ap.add_argument("--no-silero", dest="use_silero", action="store_false")
    ap.add_argument("--max-recording-s", type=int, default=300)
    ap.add_argument("--status-file", default=STATUS_FILE)
    ap.add_argument("--test-file",
                    help="Leer de un WAV mono/16kHz/S16_LE en vez del micrófono (testing automático).")
    args = ap.parse_args()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    if args.test_file:
        stream = _iter_wav_frames(args.test_file)
    else:
        cmd = _build_capture_cmd()
        stream = _iter_pipewire_frames(cmd)

    # Iniciar la captura de arecord INMEDIATAMENTE antes de cargar Silero/Torch
    # para que el buffer del kernel ALSA empiece a capturar desde t=0 real de la tecla.
    first_frames = []
    first_raw = []
    try:
        f_int, f_bytes = next(stream)
        first_frames.append(f_int)
        first_raw.append(f_bytes)
    except StopIteration:
        pass

    # Silero por defecto: sin calibración de energía que pueda contaminarse si el
    # usuario habla al arrancar (causa raíz de la primera palabra perdida).
    # Mientras Silero/Torch carga (~0.98s), arecord ya está capturando en background.
    detector = EnergyVad() if not args.use_silero else SileroVadBackend()

    calib = list(first_frames)
    calib_raw = list(first_raw)
    for _ in range(max(0, 15 - len(first_frames))):
        try:
            frames, raw = next(stream)
        except StopIteration:
            break
        calib.append(frames)
        calib_raw.append(raw)

    if isinstance(detector, EnergyVad):
        detector.calibrate(calib)
        print(f"calibrado: umbral energía = {detector.threshold:.4f}", file=sys.stderr)

    vad = SmoothedVad(detector, onset_ms=args.onset_ms, hangover_ms=args.hangover_ms,
                      prefill_ms=args.prefill_ms, max_silence_ms=args.max_silence_ms)
    # Alimentar el VAD con los frames de calibración: como grabamos desde t=0,
    # esa voz no se pierde y el estado queda caliente.
    for _frames, _raw in zip(calib, calib_raw):
        vad.push(_frames, _raw)

    started = time.time()
    stopped_by_vad = False
    written = False

    for frames, raw in stream:
        done, final_audio = vad.push(frames, raw)
        if done:
            stopped_by_vad = True
            if final_audio:
                written = _write_wav(AUDIO_FILE, final_audio)
            break
        if time.time() - started > args.max_recording_s:
            break
        if STOP_REQUESTED:
            break

    if not stopped_by_vad:
        final_audio = vad.finalize()
        if final_audio:
            written = _write_wav(AUDIO_FILE, final_audio)

    try:
        with open(args.status_file, "w") as f:
            json.dump({"stopped_by_vad": stopped_by_vad,
                       "detected": vad.speech_detected,
                       "written": written,
                       "stopped_by_signal": STOP_REQUESTED}, f)
    except Exception:
        pass


if __name__ == "__main__":
    main()
