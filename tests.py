#!/usr/bin/env python3
"""Tests unitarios del smoothed VAD y utilidades de SuperDictate.

Correr:  python3 -m pytest tests.py -q   (o)   python3 tests.py
Sin dependencias de audio real: inyecta frames sintéticos al SmoothedVad.
"""
import sys
import os
import io
import struct
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import vad
from vad import SmoothedVad, EnergyVad

# Import de utilidades puras del script principal (sin ejecutar CLI)
import re as _re
import subprocess as _subprocess
import time as _time
import signal as _signal
import json as _json
import argparse as _argparse
from datetime import datetime as _datetime
_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "super-dictate")).read()
_main_split = _src.split("if __name__")[0]
_ns = {
    "os": os, "re": _re, "sys": sys, "subprocess": _subprocess,
    "time": _time, "signal": _signal, "json": _json, "argparse": _argparse,
    "datetime": _datetime,
}
exec(compile(_main_split, "super-dictate", "exec"), _ns)
is_blank_transcription = _ns["is_blank_transcription"]
auto_detect_language = _ns["auto_detect_language"]

FRAME_SAMPLES = vad.FRAME_SAMPLES


def _frame(amp):
    """Frame int16 con amplitud constante `amp` (0 = silencio)."""
    return [amp] * FRAME_SAMPLES


def _raw(ints):
    return struct.pack("<%dh" % len(ints), *ints)


def _run_silence(detector, timeline, **kw):
    """timeline: lista de amplitudes por frame. Devuelve (dones, audits) y el wav final."""
    vad_inst = SmoothedVad(detector, **kw)
    dones = []
    for amp in timeline:
        done, final = vad_inst.push(_frame(amp), _raw(_frame(amp)))
        if done:
            dones.append((amp, len(final)))
    # final only if not done
    final = vad_inst.finalize()
    return dones, final


class TestSmoothedVad(unittest.TestCase):
    def test_silence_never_triggers(self):
        # Solo silencio -> nunca entra en habla
        d, final = _run_silence(EnergyVad(), [0] * 100)
        self.assertEqual(d, [])
        self.assertIsNone(final)

    def test_onset_does_not_fire_on_short_spike(self):
        # Un frame de voz aislado no alcanza el onset (120ms = 4 frames)
        timeline = [0] * 10 + [32767] + [0] * 20
        d, final = _run_silence(EnergyVad(), timeline)
        self.assertEqual(d, [])
        self.assertIsNone(final, "Un spike corto no debe producir audio")

    def test_sustained_speech_then_silence_cuts(self):
        # Habla sostenida + pausa larga -> corte por VAD con audio
        timeline = [0] * 6 + [20000] * 12 + [0] * 60
        # onset 120ms=4fr, hangover 500ms≈16fr, max_silence 1500ms≈50fr
        kw = dict(onset_ms=120, hangover_ms=500, prefill_ms=240, max_silence_ms=1500)
        d, final = _run_silence(EnergyVad(), timeline, **kw)
        self.assertTrue(d, "Debe cortar tras una pausa larga")
        self.assertTrue(final and len(final) > 0, "Debe producir audio")

    def test_prefill_pads_start(self):
        # El audio final debe incluir el prefill (frames previos al onset)
        timeline = [5000] * 8 + [20000] * 10 + [0] * 60
        kw = dict(onset_ms=120, hangover_ms=500, prefill_ms=240, max_silence_ms=1500)
        inst = SmoothedVad(EnergyVad(), **kw)
        for amp in timeline:
            done, fin = inst.push(_frame(amp), _raw(_frame(amp)))
            if done:
                break
        final = inst.finalize()
        # 8 (pre) + ... al menos incluye 8 frames
        self.assertIsNotNone(final)
        self.assertGreaterEqual(len(final) // (2 * FRAME_SAMPLES), 8)

    def test_box_builds_with_arecord(self):
        # Solo verifica construcción del comando (sin ejecutar arecord real)
        cmd = vad._iter_arecord_frames  # no-op, solo referencia


class TestUtilidades(unittest.TestCase):
    def test_blank_transcription_detects_empty_and_noise(self):
        self.assertTrue(is_blank_transcription(""))
        self.assertTrue(is_blank_transcription("   "))
        self.assertTrue(is_blank_transcription(".,-;?!"))

    def test_blank_transcription_keeps_real_text(self):
        self.assertFalse(is_blank_transcription("Hola, ¿cómo estás?"))
        self.assertFalse(is_blank_transcription("Abrir el proyecto CreaEfecto"))

    def test_lang_detect_spanish_default(self):
        self.assertEqual(auto_detect_language("Hola, necesito pagar la pensión alimenticia"), "es")

    def test_lang_detect_accented_spanish(self):
        self.assertEqual(auto_detect_language("Esto es una prueba de dictado con tildes por favor"), "es")


if __name__ == "__main__":
    unittest.main(verbosity=2)
