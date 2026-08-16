# TheIA Dictate 🎙️🤖

Dictado inteligente nativo de Linux (Wayland/Hyprland), homologado a los patrones de las apps de dictado consolidadas (Voxtype y similares): **push-to-talk nativo del compositor**, **auto-stop por VAD**, **OSD del compositor**, y **post-proceso por IA**.

1. **Transcripción:** captura con `arecord`, transcribe con el proveedor configurado — `local` (faster-whisper turbo, offline), `gemini` (gemini-3.7-flash) o `chirp` (Google Cloud STT, `chirp_3`).
2. **Intención y post-proceso:** Gemini arregla errores fonéticos y respeta tu vocabulario, o parsea un comando activo (abrir proyecto, consultar el vault, agregar tarea Todoist, preguntarle a Sherlock).
3. **Auto-typing:** inserta en el cursor con `wtype` (fallback: clipboard + pegar).

## Prerequisites
- `arecord` (alsa-utils), `sox` (auto-stop por silencio), `wtype`, `wl-clipboard`
- `playerctl` (opcional: pausa MPRIS al dictar)
- Para el proveedor `local`: `faster-whisper` (instalado en `.venv`)
- Para `chirp`: ADC de Google Cloud (`~/.config/gcloud/application_default_credentials_theia.json`) + proyecto con Speech API
- OSD nativo de Hyprland (`hyprctl notify`); fuera de Hyprland cae a `notify-send`

## Features
- **Push-to-talk nativo:** F9 para grabar mientras lo mantienes, soltar para transcribir (binding de Hyprland con `release = true`, igual que Voxtype). También hay toggle.
- **Modes:** diferentes prompts por contexto — email, chat, formal, notes, o custom.
- **Raw mode (`--mode raw`):** sin post-proceso, máxima velocidad.
- **Vault mode (`--mode vault`):** guarda la transcripción como nota de Obsidian en vez de pegarla.
- **Append (`--append`):** agrega al contenido del portapapeles en vez de reemplazarlo.
- **Auto-stop por VAD:** `auto_stop: "vad"` — detección de actividad de voz con onset/hangover/prefill (`vad.py`), no corta en pausas breves ni arranca con ruido. Umbral de energía adaptativo (o silero-vad si está instalado).
- **Blank transcription filter:** descarta silencios/ruido en vez de pegar basura.
- **Post-process editable:** prompts configurables en `post_process` (multi-prompt).
- **Custom Vocabulary:** tus términos (nombres, siglas, marcas) en el config.
- **Comandos por voz:** *"Abre el proyecto CreaEfecto"*, *"Abre el vault"*, *"Busca en mis notas sobre TheIA"*, *"Recuérdame llamar a cliente mañana"*, *"Dile a Sherlock que revise mi correo"*.
- **OSD de estado:** `hyprctl notify` (nativo de Hyprland) para escuchando/procesando/listo.

## Architecture

```
F9 (press) ──► arecord + VAD (vad.py)     F9 (release) / VAD auto-stop
                  │                              │
                  ▼                              ▼
             graba audio ─────────────────► recorta WAV (corte por voz)
                                                   │
                                                   ▼
                              stt_provider (local | gemini | chirp)
                                                   │
                                                   ▼
                              post_process (Gemini: corrige + intención)
                                                   │
                              ┌────────────────────┴──────────────────┐
                              ▼                                        ▼
                         insert_text (wtype)              comando (vault/todo/sherlock)
```

## Configuración

`~/.config/theia-dictate/config.json` (se crea con defaults al primer uso):

```json
{
  "language": "es",
  "default_mode": "default",
  "auto_stop": "vad",
  "stt_provider": "chirp",
  "post_process": { "enabled": true, "selected_prompt": "cleanup", "prompts": [...] },
  "vad_max_silence_ms": 2000,
  "vad_onset_ms": 120,
  "vad_hangover_ms": 400,
  "vad_prefill_ms": 240,
  "modes": { "default": { "name": "Default", "prompt": "" }, "email": { ... }, ... }
}
```

## Bindings (Hyprland)

En `~/.config/hypr/bindings.lua`, igual que el `voxtype.lua` de Omarchy:

```lua
o.bind("F9", "TheIA Dictate: grabar (push-to-talk)", "theia-dictate record start")
o.bind("F9", "TheIA Dictate: transcribir (soltar)", "theia-dictate record stop", { release = true })
o.bind("SUPER + CTRL + G", "TheIA Dictate: toggle", "theia-dictate record toggle")
o.bind("SUPER + CTRL + ESCAPE", "TheIA Dictate: cancelar", "theia-dictate record cancel")
o.bind("SUPER + CTRL + SHIFT + V", "TheIA Dictate: vault", "theia-dictate record start --mode vault")
```

## Uso

El daemon corre como servicio de usuario (`theia-dictate.service`) y se controla por señales:

```bash
theia-dictate record start|stop|toggle|cancel
theia-dictate status [--follow] [--format json]
theia-dictate record start --mode vault     # guarda en Obsidian
```

Historial de transcripciones: `~/.openclaw/workspace/memory/theia-dictate-history.jsonl`.
