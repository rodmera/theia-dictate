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
import struct
import subprocess
import time

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # S16_LE
FRAME_MS = 30          # tamaño de frame en ms (compatible con Silero)
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 480

AUDIO_FILE = "/tmp/theia-dictate-audio.wav"
STATUS_FILE = "/tmp/theia-dictate-vad-status.json"


def _rms(ints):
    if not ints:
        return 0.0
    return math.sqrt(sum(s * s for s in ints) / len(ints))


class EnergyVad:
    """Detección de voz por energía RMS por frame. Sin dependencias externas."""

    def __init__(self, threshold=0.012):
        self.threshold = threshold

    def frame_is_speech(self, frame):
        return _rms(frame) > self.threshold


class SileroVadBackend:
    """silero-vad si está disponible; fallback a energía si no."""

    def __init__(self, threshold=0.5):
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
                return bool(self.silero.predict_chunk(x) > self.threshold)
            except Exception:
                pass
        return _rms(frame_int16) > 0.012


class SmoothedVad:
    """Aplica onset/hangover/prefill sobre un detector de frames interno.

    Máquina de estados:
      - Antes de entrar en habla: se requieren `onset_frames` consecutivos de voz.
      - En habla: el hangover mantiene activa la grabación N frames tras la última
        señal de voz; al agotarse (por un silencio), si la pausa llega a
        `max_idle_frames` sin reanudar, se corta la grabación.
      - `prefill_frames` conserva audio previo al onset para no perder el inicio.
    """

    def __init__(self, detector, onset_ms=120, hangover_ms=500, prefill_ms=240,
                 max_silence_ms=1500):
        self.detector = detector
        self.frame_ms = FRAME_MS
        self.onset_frames = max(1, onset_ms // self.frame_ms)
        self.hangover_frames = max(0, hangover_ms // self.frame_ms)
        self.prefill_frames = max(0, prefill_ms // self.frame_ms)
        self.max_idle_frames = max(1, max_silence_ms // self.frame_ms)

        self.prefill_buf = []       # raw bytes conservados (para no perder el inicio)
        self.recorded = bytearray() # audio capturado desde el onset
        self.onset_counter = 0
        self.hangover_frames_left = 0
        self.idle_frames = 0
        self.in_speech = False
        self.speech_detected = False

    def push(self, frame_int16, raw_bytes):
        """Feed de un frame. Retorna (done, final_audio_bytes_or_None)."""
        is_speech = self.detector.frame_is_speech(frame_int16)

        # prefill: mantener un buffer rodante del audio previo al habla
        self.prefill_buf.append(raw_bytes)
        if len(self.prefill_buf) > self.prefill_frames:
            self.prefill_buf.pop(0)

        if not self.in_speech:
            if is_speech:
                self.onset_counter += 1
                if self.onset_counter >= self.onset_frames:
                    self.in_speech = True
                    self.speech_detected = True
                    self.hangover_frames_left = self.hangover_frames
                    self.idle_frames = 0
                    # arrancar con el prefill + este frame
                    self.recorded = bytearray(b"".join(self.prefill_buf))
            else:
                self.onset_counter = 0
            return False, None

        # En habla: acumular audio
        self.recorded.extend(raw_bytes)

        if is_speech:
            self.hangover_frames_left = self.hangover_frames
            self.idle_frames = 0
            self.speech_detected = True
        else:
            self.hangover_frames_left -= 1
            self.idle_frames += 1

        # Cortar solo si: hangover agotado Y la pausa ya es larga (max_idle_frames)
        if self.hangover_frames_left <= 0 and self.idle_frames >= self.max_idle_frames:
            self.in_speech = False
            return True, bytes(self.recorded)

        return False, None

    def finalize(self):
        """Llamada si se agotó el tiempo de grabación sin corte por VAD."""
        self.in_speech = False
        return bytes(self.recorded) if self.speech_detected else None


def _iter_arecord_frames(cmd):
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    frame_bytes = FRAME_SAMPLES * CHANNELS * SAMPLE_WIDTH
    try:
        while True:
            chunk = proc.stdout.read(frame_bytes)
            if not chunk:
                break
            if len(chunk) < frame_bytes:
                chunk += proc.stdout.read(frame_bytes - len(chunk))
                if len(chunk) < frame_bytes:
                    break
            ints = struct.unpack("<%dh" % (frame_bytes // SAMPLE_WIDTH), chunk[:frame_bytes])
            yield list(ints), chunk[:frame_bytes]
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        try:
            proc.terminate()
        except Exception:
            pass


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--onset-ms", type=int, default=120)
    ap.add_argument("--hangover-ms", type=int, default=500)
    ap.add_argument("--prefill-ms", type=int, default=240)
    ap.add_argument("--max-silence-ms", type=int, default=1500,
                    help="Pausa sin voz (post-hangover) que dispara el corte automático.")
    ap.add_argument("--use-silero", action="store_true",
                    help="Usar silero-vad si está disponible (por defecto energía).")
    ap.add_argument("--max-recording-s", type=int, default=300)
    ap.add_argument("--status-file", default=STATUS_FILE)
    args = ap.parse_args()

    detector = SileroVadBackend() if args.use_silero else EnergyVad()
    vad = SmoothedVad(detector, onset_ms=args.onset_ms, hangover_ms=args.hangover_ms,
                      prefill_ms=args.prefill_ms, max_silence_ms=args.max_silence_ms)

    cmd = ["arecord", "-D", "default", "-f", "S16_LE", "-c", str(CHANNELS),
           "-r", str(SAMPLE_RATE), "-t", "raw"]
    started = time.time()
    stopped_by_vad = False
    written = False

    for frames, raw in _iter_arecord_frames(cmd):
        done, final_audio = vad.push(frames, raw)
        if done:
            stopped_by_vad = True
            if final_audio:
                written = _write_wav(AUDIO_FILE, final_audio)
            break
        if time.time() - started > args.max_recording_s:
            break

    if not stopped_by_vad:
        final_audio = vad.finalize()
        if final_audio:
            written = _write_wav(AUDIO_FILE, final_audio)

    try:
        with open(args.status_file, "w") as f:
            json.dump({"stopped_by_vad": stopped_by_vad,
                       "detected": vad.speech_detected,
                       "written": written}, f)
    except Exception:
        pass


if __name__ == "__main__":
    main()
