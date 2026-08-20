"""Módulo de transcripción STT con soporte multimodal Gemini 3.7 Flash y fallbacks.

Implementa la transcripción de audio desacoplada para TheIA Dictate y TheIA Notes.
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def _wav_with_leading_silence(audio_path: str, silence_ms: int = 150) -> bytes:
    """Lee el archivo WAV agregando un pequeño silencio inicial si es necesario."""
    if not os.path.exists(audio_path):
        return b""
    with open(audio_path, "rb") as f:
        return f.read()


def _gemini_transcribe(audio_path: str, language: str = "es", model: str = "gemini-3.7-flash") -> dict[str, Any]:
    """Transcripción directa de audio con Gemini 3.7 Flash."""
    sys.path.insert(0, os.path.expanduser("~/.openclaw/workspace/skills"))
    try:
        from shared_config import GEMINI_API_KEY
    except Exception as key_err:
        return {"error": f"shared_config/GEMINI_API_KEY: {key_err}"}

    if not GEMINI_API_KEY:
        # Fallback a variable de entorno o .env
        GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

    if not GEMINI_API_KEY:
        return {"error": "GEMINI_API_KEY no configurada"}

    if not os.path.exists(audio_path):
        return {"error": f"Archivo no encontrado: {audio_path}"}

    raw_wav = _wav_with_leading_silence(audio_path)
    if not raw_wav:
        return {"error": f"Archivo de audio vacío o no legible: {audio_path}"}

    audio_b64 = base64.b64encode(raw_wav).decode()

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [
            {
                "parts": [
                    {"inline_data": {"mime_type": "audio/wav", "data": audio_b64}},
                    {
                        "text": (
                            f"Transcribe el audio de voz en {language}. Devuelve el texto "
                            "transcrito fielmente con puntuación adecuada."
                        )
                    },
                ]
            }
        ],
        "generationConfig": {"temperature": 0.1},
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as http_err:
        body = http_err.read().decode(errors="replace")
        return {"error": f"Gemini HTTP {http_err.code}: {body[:300]}"}
    except Exception as net_err:
        return {"error": f"Gemini red: {net_err}"}

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError):
        return {"error": f"Gemini respuesta sin texto: {str(data)[:300]}"}

    if not text:
        return {"error": "Gemini no devolvió texto (¿audio silencioso?)"}
    return {"text": text, "provider": "gemini", "model": model}


def _local_transcribe(audio_path: str, language: str = "es") -> dict[str, Any]:
    """Transcripción con Whisper local de fallback."""
    if not os.path.exists(audio_path):
        return {"error": f"Archivo no encontrado: {audio_path}"}
    try:
        sys.path.insert(0, os.path.expanduser("~/.openclaw/workspace/skills/stt/scripts"))
        from stt import transcribe
        res = transcribe(audio_path, model_size="turbo", language=language, beam_size=5)
        if isinstance(res, dict):
            if "provider" not in res:
                res["provider"] = "local"
            return res
        return {"text": str(res), "provider": "local", "model": "turbo"}
    except Exception as transcribe_err:
        return {"error": f"Whisper local: {transcribe_err}"}


def transcribe_audio(
    audio_path: str,
    language: str = "es",
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Transcribe audio usando Gemini 3.7 Flash como motor principal con fallback a local."""
    cfg = config or {}
    primary = cfg.get("stt_provider", "gemini")
    model = cfg.get("stt_gemini_model", "gemini-3.7-flash")

    # 1. Intentar Gemini
    if primary == "gemini" or primary != "local":
        res = _gemini_transcribe(audio_path, language=language, model=model)
        if res.get("text") and not res.get("error"):
            return res

    # 2. Fallback a Local
    res_local = _local_transcribe(audio_path, language=language)
    if res_local.get("text") and not res_local.get("error"):
        return res_local

    return res if "res" in locals() and res.get("error") else res_local
