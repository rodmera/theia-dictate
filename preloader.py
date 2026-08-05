#!/usr/bin/env python3
"""Pre-carga el modelo Whisper durante la grabación para eliminar cold start.

Flujo:
  1. Carga WhisperModel en memoria (fase costosa, ~9s, corre mientras el usuario habla)
  2. Espera señal READY_FILE (= usuario paró la grabación)
  3. Transcribe con el modelo ya cargado (~0.2s) y escribe resultado en RESULT_FILE
"""
import os
import sys
import json
import time

AUDIO_FILE     = '/tmp/theia-dictate-audio.wav'
READY_FILE     = '/tmp/theia-dictate-audio-ready'
RESULT_FILE    = '/tmp/theia-dictate-transcription.json'
MODEL_READY    = '/tmp/theia-dictate-model-ready'
MAX_WAIT_S     = 120   # tiempo máx esperando que el usuario pare (2 min)

sys.path.insert(0, os.path.expanduser('~/.openclaw/workspace/skills/stt/scripts'))


def main():
    try:
        from stt import _get_model, transcribe

        # Fase 1 — cargar modelo (corre en paralelo mientras el usuario habla)
        _get_model('turbo')
        open(MODEL_READY, 'w').close()

        # Fase 2 — esperar señal de audio listo
        deadline = time.time() + MAX_WAIT_S
        while time.time() < deadline:
            if os.path.exists(READY_FILE):
                break
            time.sleep(0.05)
        else:
            return  # timeout: usuario nunca paró la grabación

        try:
            os.remove(READY_FILE)
        except FileNotFoundError:
            pass

        # Fase 3 — transcribir (modelo ya en memoria, rápido)
        language = 'es'
        try:
            with open('/tmp/theia-dictate-lang') as f:
                language = f.read().strip()
        except FileNotFoundError:
            pass
        result = transcribe(AUDIO_FILE, model_size='turbo', language=language, beam_size=5)

        with open(RESULT_FILE, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False)

    except Exception as ex:
        try:
            with open(RESULT_FILE, 'w', encoding='utf-8') as f:
                json.dump({'error': str(ex), 'text': ''}, f, ensure_ascii=False)
        except Exception:
            pass


if __name__ == '__main__':
    main()
