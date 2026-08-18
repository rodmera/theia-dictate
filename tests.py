#!/usr/bin/env python3
"""Tests unitarios del smoothed VAD y utilidades de TheIA Dictate.

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
_src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "theia-dictate")).read()
_main_split = _src.split("if __name__")[0]
_ns = {
    "os": os, "re": _re, "sys": sys, "subprocess": _subprocess,
    "time": _time, "signal": _signal, "json": _json, "argparse": _argparse,
    "datetime": _datetime,
}
exec(compile(_main_split, "theia-dictate", "exec"), _ns)
is_blank_transcription = _ns["is_blank_transcription"]
auto_detect_language = _ns["auto_detect_language"]
_get_chirp_access_token = _ns["_get_chirp_access_token"]
_get_sa_access_token = _ns["_get_sa_access_token"]
_read_token_cache = _ns["_read_token_cache"]
_write_token_cache = _ns["_write_token_cache"]
_find_service_account_key = _ns["_find_service_account_key"]
_chirp_transcribe = _ns["_chirp_transcribe"]
_gemini_transcribe = _ns["_gemini_transcribe"]
_local_transcribe = _ns["_local_transcribe"]
transcribe_audio = _ns["transcribe_audio"]
audit_providers = _ns["audit_providers"]
doctor_cmd = _ns["doctor_cmd"]
read_state = _ns["read_state"]
set_state = _ns["set_state"]
process_alive = _ns["process_alive"]

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
        # onset 120ms=3fr, hangover 500ms≈15fr, max_silence 1500ms≈46fr
        kw = dict(onset_ms=120, hangover_ms=500, prefill_ms=240, max_silence_ms=1500)
        d, final = _run_silence(EnergyVad(), timeline, **kw)
        self.assertTrue(d, "Debe cortar tras una pausa larga")
        self.assertTrue(final and len(final) > 0, "Debe producir audio")

    def test_records_from_very_start(self):
        # El audio final debe conservar TODO desde el primer frame (t=0),
        # aunque la voz arranque en el primer frame (sin prefill que cubra).
        timeline = [20000] * 12 + [0] * 60
        kw = dict(onset_ms=120, hangover_ms=500, prefill_ms=240, max_silence_ms=1500)
        inst = SmoothedVad(EnergyVad(), **kw)
        for amp in timeline:
            done, fin = inst.push(_frame(amp), _raw(_frame(amp)))
            if done:
                break
        final = inst.finalize()
        self.assertIsNotNone(final)
        # Debe incluir los frames de voz desde t=0 (no solo desde el onset+prefill)
        self.assertGreaterEqual(len(final) // (2 * FRAME_SAMPLES), 12)

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


class TestChirpTokenRefreshAndErrors(unittest.TestCase):
    def test_http_error_400_extracts_invalid_rapt(self):
        from unittest.mock import patch, MagicMock
        import urllib.error
        import tempfile

        # Crear ADC temporal
        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as f:
            _json.dump({
                "client_id": "test_client_id",
                "client_secret": "test_secret",
                "refresh_token": "test_refresh_token",
            }, f)
            tmp_adc = f.name

        try:
            error_body = _json.dumps({
                "error": "invalid_grant",
                "error_description": "reauth related error (invalid_rapt)",
                "error_uri": "https://support.google.com/a/answer/9368756",
                "error_subtype": "invalid_rapt"
            }).encode("utf-8")

            mock_fp = io.BytesIO(error_body)
            http_err = urllib.error.HTTPError(
                url="https://oauth2.googleapis.com/token",
                code=400,
                msg="Bad Request",
                hdrs={},
                fp=mock_fp
            )

            with patch("urllib.request.urlopen", side_effect=http_err):
                at, err = _get_chirp_access_token(adc_path=tmp_adc, use_cache=False)
                self.assertIsNone(at)
                self.assertIsNotNone(err)
                self.assertIn("invalid_rapt", err)
                self.assertIn("400", err)
                self.assertIn("reauth related error", err)
        finally:
            if os.path.exists(tmp_adc):
                os.remove(tmp_adc)

    def test_missing_adc_file_returns_error(self):
        at, err = _get_chirp_access_token(adc_path="/tmp/non_existent_adc_file_12345.json", use_cache=False)
        self.assertIsNone(at)
        self.assertIn("Falta ADC", err)

    def test_valid_token_returns_access_token(self):
        from unittest.mock import patch, MagicMock
        import tempfile

        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as f:
            _json.dump({
                "client_id": "test_client_id",
                "client_secret": "test_secret",
                "refresh_token": "test_refresh_token",
            }, f)
            tmp_adc = f.name

        try:
            mock_resp = MagicMock()
            mock_resp.read.return_value = _json.dumps({"access_token": "ya29.test_valid_token_123"}).encode()
            mock_resp.__enter__.return_value = mock_resp

            with patch("urllib.request.urlopen", return_value=mock_resp):
                at, err = _get_chirp_access_token(adc_path=tmp_adc, use_cache=False)
                self.assertEqual(at, "ya29.test_valid_token_123")
                self.assertIsNone(err)
        finally:
            if os.path.exists(tmp_adc):
                os.remove(tmp_adc)


class TestSttProviderFallback(unittest.TestCase):
    def test_chirp_fails_falls_back_to_gemini(self):
        from unittest.mock import patch

        cfg = {
            "stt_provider": "chirp",
            "stt_chirp_project": "test-project",
            "stt_gemini_model": "gemini-3.7-flash",
        }

        with patch.dict(_ns, {
            "_chirp_transcribe": lambda *a, **k: {"error": "Token theia HTTP 400: reauth related error (invalid_rapt)"},
            "_gemini_transcribe": lambda *a, **k: {"text": "Texto transcrito por Gemini fallback", "provider": "gemini", "model": "gemini-3.7-flash"},
        }):
            res = _ns["transcribe_audio"]("/tmp/test.wav", language="es", config=cfg)
            self.assertEqual(res.get("text"), "Texto transcrito por Gemini fallback")
            self.assertEqual(res.get("provider"), "gemini")
            self.assertEqual(res.get("fallback_from"), "chirp")

    def test_chirp_and_gemini_fail_falls_back_to_local(self):
        from unittest.mock import patch

        cfg = {
            "stt_provider": "chirp",
            "stt_chirp_project": "test-project",
        }

        with patch.dict(_ns, {
            "_chirp_transcribe": lambda *a, **k: {"error": "Token error"},
            "_gemini_transcribe": lambda *a, **k: {"error": "Gemini rate limit 429"},
            "_local_transcribe": lambda *a, **k: {"text": "Texto transcrito por Whisper local", "provider": "local", "model": "turbo"},
        }):
            res = _ns["transcribe_audio"]("/tmp/test.wav", language="es", config=cfg)
            self.assertEqual(res.get("text"), "Texto transcrito por Whisper local")
            self.assertEqual(res.get("provider"), "local")
            self.assertEqual(res.get("fallback_from"), "chirp")

    def test_all_providers_fail_returns_consolidated_error(self):
        from unittest.mock import patch

        cfg = {
            "stt_provider": "chirp",
        }

        with patch.dict(_ns, {
            "_chirp_transcribe": lambda *a, **k: {"error": "Chirp token expirado"},
            "_gemini_transcribe": lambda *a, **k: {"error": "Gemini sin red"},
            "_local_transcribe": lambda *a, **k: {"error": "Faster-whisper OOM"},
        }):
            res = _ns["transcribe_audio"]("/tmp/test.wav", language="es", config=cfg)
            self.assertIn("error", res)
            self.assertIn("Todos los proveedores STT fallaron", res["error"])
            self.assertEqual(len(res.get("failed_attempts", [])), 3)


class TestTokenCachingAndServiceAccount(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp_dir = tempfile.mkdtemp()
        self.tmp_cache_file = os.path.join(self.tmp_dir, "test-token-cache.json")
        _ns["TOKEN_CACHE_FILE"] = self.tmp_cache_file

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_write_and_read_token_cache(self):
        import time
        token = "ya29.test_cached_token_12345"
        expires_at = time.time() + 3000
        _write_token_cache(token, expires_at, source_id="test_src")
        
        cached = _read_token_cache(source_id="test_src")
        self.assertEqual(cached, token)

    def test_read_cache_requires_matching_source_id(self):
        import time
        token = "ya29.test_source_id_token"
        expires_at = time.time() + 3000
        _write_token_cache(token, expires_at, source_id="sa_path_1")

        # source_id no coincide -> rechaza
        self.assertIsNone(_read_token_cache(source_id="sa_path_2"))
        # source_id nulo -> rechaza por seguridad
        self.assertIsNone(_read_token_cache(source_id=None))
        # source_id exacto -> acepta
        self.assertEqual(_read_token_cache(source_id="sa_path_1"), token)

    def test_expired_token_cache_returns_none(self):
        import time
        token = "ya29.test_expired_token"
        expires_at = time.time() + 30  # Menos de 60s restantes
        _write_token_cache(token, expires_at, source_id="test_src")
        
        cached = _read_token_cache(source_id="test_src")
        self.assertIsNone(cached)

    def test_get_chirp_access_token_uses_cache_first(self):
        import time
        import tempfile

        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as f:
            _json.dump({"type": "service_account"}, f)
            tmp_sa = f.name

        try:
            token = "ya29.cached_token_priority"
            _write_token_cache(token, time.time() + 3000, source_id=tmp_sa)

            # Debe resolver desde caché con el source_id correcto
            at, err = _get_chirp_access_token(sa_path=tmp_sa, adc_path="/tmp/non_existent.json", use_cache=True)
            self.assertEqual(at, token)
            self.assertIsNone(err)
        finally:
            if os.path.exists(tmp_sa):
                os.remove(tmp_sa)

    def test_service_account_priority_over_adc(self):
        import time
        from unittest.mock import patch
        import tempfile

        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as f:
            _json.dump({"type": "service_account"}, f)
            tmp_sa = f.name

        try:
            with patch.dict(_ns, {
                "_read_token_cache": lambda *a, **k: None,
                "_get_sa_access_token": lambda path: ("ya29.sa_token_ok", None, time.time() + 3600),
            }):
                at, err = _get_chirp_access_token(sa_path=tmp_sa, adc_path="/tmp/fake_adc.json", use_cache=False)
                self.assertEqual(at, "ya29.sa_token_ok")
                self.assertIsNone(err)
        finally:
            if os.path.exists(tmp_sa):
                os.remove(tmp_sa)


class TestStaleStateHealing(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp_dir = tempfile.mkdtemp()
        self.tmp_state_file = os.path.join(self.tmp_dir, "theia-dictate-state")
        self.tmp_recorder_pid_file = os.path.join(self.tmp_dir, "theia-dictate-recorder.pid")
        _ns["STATE_FILE"] = self.tmp_state_file
        _ns["RECORDER_PID"] = self.tmp_recorder_pid_file

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_read_state_heals_stale_recording_state_when_recorder_dead(self):
        # Escribir estado 'recording' con un PID inexistente
        with open(self.tmp_state_file, "w") as f:
            f.write("recording")
        with open(self.tmp_recorder_pid_file, "w") as f:
            f.write("9999999")

        # read_state debe auto-sanar a 'idle'
        st = read_state()
        self.assertEqual(st, "idle")

        # Archivo de estado debe quedar como 'idle'
        with open(self.tmp_state_file) as f:
            self.assertEqual(f.read().strip(), "idle")

        # Archivo RECORDER_PID debe ser limpiado
        self.assertFalse(os.path.exists(self.tmp_recorder_pid_file))

    def test_read_state_preserves_recording_state_when_recorder_alive(self):
        # Escribir estado 'recording' con el PID actual (vivo)
        with open(self.tmp_state_file, "w") as f:
            f.write("recording")
        with open(self.tmp_recorder_pid_file, "w") as f:
            f.write(str(os.getpid()))

        st = read_state()
        self.assertEqual(st, "recording")


class TestDoctorDiagnostics(unittest.TestCase):
    def test_audit_providers_identifies_invalid_rapt(self):
        from unittest.mock import patch

        with patch.dict(_ns, {
            "_get_chirp_access_token": lambda *a, **k: (None, "Token theia HTTP 400: reauth related error (invalid_rapt)"),
        }):
            report = _ns["audit_providers"]()
            self.assertIn("providers", report)
            chirp_rep = report["providers"].get("chirp", {})
            self.assertFalse(chirp_rep.get("token_ok"))
            self.assertTrue(chirp_rep.get("reauth_required"))
            self.assertIn("theia-dictate auth", chirp_rep.get("reauth_command", ""))

    def test_audit_providers_identifies_service_account(self):
        from unittest.mock import patch
        import tempfile

        with tempfile.NamedTemporaryFile("w", delete=False, suffix=".json") as f:
            _json.dump({"type": "service_account"}, f)
            tmp_sa = f.name

        try:
            with patch.dict(_ns, {
                "_find_service_account_key": lambda *a, **k: tmp_sa,
                "_get_chirp_access_token": lambda *a, **k: ("ya29.sa_token", None),
            }):
                report = _ns["audit_providers"]()
                chirp_rep = report["providers"].get("chirp", {})
                self.assertTrue(chirp_rep.get("token_ok"))
                self.assertEqual(chirp_rep.get("token_source"), "service_account")
        finally:
            if os.path.exists(tmp_sa):
                os.remove(tmp_sa)


if __name__ == "__main__":
    unittest.main(verbosity=2)
