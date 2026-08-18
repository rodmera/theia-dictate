# TheIA Dictate — Notas para Claude Code

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
implementaciones `linux` / `macos`. El script `theia-dictate` principal no
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
| **Notificaciones de estado** | `notify-send` estándar (no usar OSD de hyprctl — se ve horrible) |
| **Push-to-talk nativo** | Binding de Hyprland con `release = true` (F9) — el compositor hace el PTT, no un daemon evdev |
| **Daemon + señales** | `--daemon` (servicio systemd --user) — control por señales: `record start\|stop\|toggle\|cancel` (SIGUSR1/SIGUSR2) |
| **Status para la barra** | `status [--follow] [--format json]` — estado idle/recording/transcribing |
| **Inserción por portapapeles** | `insert_text()` copia con `wl-copy` y pega `Ctrl+Shift+V` vía wtype (estándar de las apps consolidadas); NUNCA teclear carácter por carácter (wtype pierde puntuación/números). Fallback: pegado virtual UInput |
| **Pausa MPRIS** | Pausa los reproductores con `playerctl -a pause` al grabar; reanuda al terminar (guard si playerctl falta) |
| **Fallback STT robusto** | Si el proveedor principal falla por credenciales o red, cae automáticamente a Gemini o local Whisper |
| **Diagnóstico (`doctor`)** | `theia-dictate doctor [--format json]` — audita Service Account, credenciales ADC / RAPT, API keys, herramientas y daemon |
| **Reautenticación One-Click (`auth`)** | `theia-dictate auth` (alias: `login`) — asistente OAuth 2.0 interactivo one-click en navegador con loopback local |
| **Service Account & Token Caching** | Soporte para `chirp-sa-key.json` (inmune a RAPT) y caché de token en disco (50 min TTL) para baja latencia |
| **RingRecorder & Pre-roll** | Captura continua en background con ringbuffer circular de 1.0s (mono 16kHz S16_LE) para latencia 0ms y captura de audio previo a la pulsación |
| **Audio Feedback** | Beeps/pops sutiles no bloqueantes al iniciar (`start`) y detener (`stop`) la captura (configurable en `audio_feedback`) |
| **Barra Omarchy / Quickshell** | `status [--follow] [--format json] [--extended]` emite JSON compatible con `Dictation.qml` y `omarchy-voxtype-status` |

## Archivos del proyecto

| Archivo | Función |
|---|---|
| `theia-dictate` | Script principal |
| `preloader.py` | Pre-carga Whisper en background |
| `silence_watcher.py` | Monitorea PID de sox y dispara stop cuando termina |
| `vad.py` | VAD con suavizado (onset/hangover/prefill) — energía o silero-vad |
| `vad_watcher.py` | Espera el corte del VAD y dispara la transcripción |
| `tests.py` | Tests unitarios (VAD, filtro blanco, detección de idioma) |

> Nota (2026-08-16): `ptt.py`, `overlay.py` e `indicator.py` fueron eliminados — el PTT lo hace el compositor (binding `release=true`). No revivirlos. Las notificaciones son `notify-send` estándar, no el OSD de hyprctl.

## Configuración

Config en `~/.config/theia-dictate/config.json` (se crea automáticamente al primer uso):
- `language`: idioma para Whisper (ej. "es", "en", "pt"). Pasa al pre-loader vía `/tmp/theia-dictate-lang`.
- `default_mode`: modo por defecto si no se pasa `--mode`.
- `vocabulary`: lista de términos propios inyectados al prompt de Gemini (nombres, siglas, marcas).
- `modes`: dict de modos. Cada modo tiene `name` y `prompt` que se añade al prompt de refined_text.
- `auto_stop`: `"silence"` (sox, default) | `"vad"` (VAD suavizado). Con VAD: `vad_max_silence_ms`, `vad_onset_ms`, `vad_hangover_ms`, `vad_prefill_ms`, `vad_use_silero`.
- `post_process`: `{ enabled, prompts: [{id,name,prompt}], selected_prompt }` — `--prompts` para listar, editar en el JSON.
- `llm_provider`: `{ provider: "gemini"|"openai", openai_base_url, openai_api_key, openai_model, lang_detect }`.

Para agregar un modo custom, editar el JSON y añadir una clave nueva bajo `modes`.
Los modos se activan con `--mode <clave>` o se configuran como `default_mode`.

## Arquitectura — Pre-loader

- `preloader.py` corre en background desde la primera pulsación
- Carga WhisperModel turbo (~3.7s) mientras el usuario habla
- Señaliza vía `/tmp/theia-dictate-audio-ready` al parar la grabación
- Latencia post-segunda-pulsación: ~10s (vs ~14s sin pre-loader)
- Bottleneck restante: inferencia CPU ~5.4s (irreducible sin GPU/modelo más pequeño)

## Paste en Linux/Wayland

`insert_text()` intenta `wtype` (Hyprland/wlroots, funciona) y solo si falla cae a
clipboard + paste por UInput (evdev). Inspiración Voxtype.

## Daemon + control por señales (estilo Voxtype, 2026-08-15)

El script corre como daemon residente gestionado por systemd --user:

```bash
systemctl --user start theia-dictate     # daemon (se inicia solo con la sesión)
theia-dictate record toggle              # keybind SUPER+CTRL+G (Grabar)
theia-dictate record start|stop|toggle|cancel
theia-dictate status [--follow] [--format json]
```

- Señales: SIGUSR1 = start, SIGUSR2 = stop&transcribir (cancel = archivo de cancelación).
- Estado en `/tmp/theia-dictate-state` (idle|recording|transcribing).
- El recorder (sox/arecord/vad) es subproceso del daemon; su PID va en `/tmp/theia-dictate-recorder.pid`.
- **Carrera conocida (arreglada):** `silence_watcher` SIEMPRE manda `record stop` (nunca toggle) —
  un toggle tras el stop reiniciaba la grabación fantasma.
- `on_start` solo arranca desde estado `idle` (evita señales encoladas que reinicien).

## Push-to-talk nativo (2026-08-16, homologado a Voxtype)

El PTT lo hace el compositor, no un daemon: en `~/.config/hypr/bindings.lua`,
dos bindings sobre F9 — uno normal (start) y otro con `{ release = true }` (stop).
Mismo patrón que el `voxtype.lua` de Omarchy. SUPER+CTRL+G queda como toggle.

```lua
o.bind("F9", "TheIA Dictate: grabar (push-to-talk)", "theia-dictate record start")
o.bind("F9", "TheIA Dictate: transcribir (soltar)", "theia-dictate record stop", { release = true })
```

## Entorno Python (venv)

El proyecto corre con el venv `./.venv` (`--system-site-packages`). El script
se re-ejecuta solo con el python del venv si `faster_whisper` no es importable
(bootstrap al inicio de `__main__`). Crear el venv:

```bash
python3 -m venv --system-site-packages .venv
.venv/bin/pip install faster-whisper
```

Deps de sistema: `python-evdev`, `sox`, `zenity` (Arch); `playerctl` opcional (pausa MPRIS).

## 🔒 Prevención de colisiones entre agentes

Tres agentes de coding pueden editar este repo (Claude, Pi, Codex). Para evitar pisarse:
- **Siempre usar el lock antes de empezar a trabajar:**
  ```python
  from lock_agent import repo_lock, LockHeld
  with repo_lock("REPO_PATH") as info:
      # trabajar aquí
  ```
- Si ves `LockHeld`, otro agente ya está trabajando: aborta o coordiná con él.
