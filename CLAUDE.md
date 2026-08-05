# SuperDictate — Notas para Claude Code

## Port a macOS (pendiente)

El core (Whisper, pre-loader, Gemini) es multiplataforma y no necesita cambios.
Solo hay que reemplazar 5 dependencias Linux-específicas:

| Componente | Linux | macOS |
|---|---|---|
| Grabación | `arecord` | `sounddevice` + `soundfile` (Python) o `sox rec` |
| Paste | `evdev` / `UInput` | `pyautogui` (Quartz, funciona directo) |
| Portapapeles | `wl-copy` | `pbcopy` |
| Notificaciones | `notify-send` | `osascript -e 'display notification...'` |
| Abrir apps/archivos | `xdg-open` | `open` |
| Diálogo RAG | `zenity` | `osascript` AppleScript dialog |

**Approach recomendado:** abstraer las 5 funciones en `platform_utils.py` con
implementaciones `linux` / `macos`. El script `super-dictate` principal no
necesita cambios estructurales.

**No reescribir en Swift.** Python en macOS tiene soporte nativo completo.
Swift solo tendría sentido para distribución en Mac App Store, lo que implicaría
sandboxing y haría el keystroke injection más difícil.

**Esfuerzo estimado:** 2-3 horas.

## Features implementadas

| Feature | Cómo activar |
|---|---|
| Modo raw (bypass Gemini) | `--mode raw` |
| Modo vault (guardar en Obsidian) | `--mode vault` — llama `capture_note()` del skill obsidian-capture |
| Append al portapapeles | `--append` — lee `wl-paste` y prepende al texto final |
| Historial | `--history [N]` — imprime en terminal las últimas N entradas del JSONL |
| Auto-stop por silencio | `silence_timeout: N` en config — usa sox + silence_watcher.py |
| Auto-stop por VAD (suavizado) | `auto_stop: "vad"` en config — usa `vad.py` (onset/hangover/prefill, energía o silero-vad) + vad_watcher.py |
| Filtro de transcripción en blanco | automático tras transcribir — descarta ruido/puntuación suelta (`is_blank_transcription`) |
| Post-proceso editable (multi-prompt) | `post_process.enabled: true` + `post_process.prompts` / `--prompts` — estilo Handy |
| Proveedor LLM configurable | `llm_provider.provider: "openai"` (OpenAI-compatible) con `lang_detect` |
| Overlay de estado | `overlay: "tray" | "overlay"` en config — usa `overlay.py` |
| Push-to-talk | `ptt.py` daemon — monitorea tecla configurable con evdev |

## Archivos del proyecto

| Archivo | Función |
|---|---|
| `super-dictate` | Script principal |
| `preloader.py` | Pre-carga Whisper en background |
| `silence_watcher.py` | Monitorea PID de sox y dispara stop cuando termina |
| `vad.py` | VAD con suavizado (onset/hangover/prefill) — energía o silero-vad |
| `vad_watcher.py` | Espera el corte del VAD y dispara la transcripción |
| `overlay.py` | Overlay/tray de estado durante la grabación |
| `ptt.py` | Daemon push-to-talk |
| `indicator.py` | Icono de tray mientras graba |
| `tests.py` | Tests unitarios (VAD, filtro blanco, detección de idioma) |

## Configuración

Config en `~/.config/superdictate/config.json` (se crea automáticamente al primer uso):
- `language`: idioma para Whisper (ej. "es", "en", "pt"). Pasa al pre-loader vía `/tmp/super-dictate-lang`.
- `default_mode`: modo por defecto si no se pasa `--mode`.
- `vocabulary`: lista de términos propios inyectados al prompt de Gemini (nombres, siglas, marcas).
- `modes`: dict de modos. Cada modo tiene `name` y `prompt` que se añade al prompt de refined_text.
- `auto_stop`: `"silence"` (sox, default) | `"vad"` (VAD suavizado). Con VAD: `vad_max_silence_ms`, `vad_onset_ms`, `vad_hangover_ms`, `vad_prefill_ms`, `vad_use_silero`.
- `post_process`: `{ enabled, prompts: [{id,name,prompt}], selected_prompt }` — `--prompts` para listar, editar en el JSON.
- `llm_provider`: `{ provider: "gemini"|"openai", openai_base_url, openai_api_key, openai_model, lang_detect }`.
- `overlay`: `"tray"` (default) | `"overlay"`.

Para agregar un modo custom, editar el JSON y añadir una clave nueva bajo `modes`.
Los modos se activan con `--mode <clave>` o se configuran como `default_mode`.

## Arquitectura — Pre-loader

- `preloader.py` corre en background desde la primera pulsación
- Carga WhisperModel turbo (~3.7s) mientras el usuario habla
- Señaliza vía `/tmp/super-dictate-audio-ready` al parar la grabación
- Latencia post-segunda-pulsación: ~10s (vs ~14s sin pre-loader)
- Bottleneck restante: inferencia CPU ~5.4s (irreducible sin GPU/modelo más pequeño)

## Paste en Linux/Wayland

Se usa `evdev`/`UInput` (Ctrl+Shift+V) porque en GNOME Wayland no hay
alternativa universal:
- `wtype` requiere protocolo wlroots (no disponible en GNOME)
- `ydotool` versión repos Ubuntu no soporta Unicode sin daemon
- `gdbus Shell.Eval` deshabilitado en GNOME moderno

## 🔒 Prevención de colisiones entre agentes

Tres agentes de coding pueden editar este repo (Claude, Pi, Codex). Para evitar pisarse:
- **Siempre usar el lock antes de empezar a trabajar:**
  ```python
  from lock_agent import repo_lock, LockHeld
  with repo_lock("REPO_PATH") as info:
      # trabajar aquí
  ```
- Si ves `LockHeld`, otro agente ya está trabajando: aborta o coordiná con él.
