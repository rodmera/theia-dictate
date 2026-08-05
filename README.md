# TheIA Dictate 🎙️🤖

TheIA Dictate is a Linux-native, Wayland-compatible smart dictation tool. It uses a hybrid pipeline to achieve near-perfect transcription, auto-typing, and intelligent command routing.

1. **Local Transcription:** Captures audio using `arecord` and transcribes it locally with `faster-whisper` (turbo model). A background pre-loader warms the model while you speak, eliminating cold-start latency (~3.7s saved).
2. **Intent Analysis & Cloud Refinement:** Sends the raw transcription to **Gemini Flash**. The AI routes the audio to either:
   - **Passive Dictation:** Fixes phonetic errors (understanding your local context, projects, and ecosystem names) and pastes the text.
   - **Active Command:** Parses the command and executes actions locally (e.g. open projects, query the Obsidian Vault, add Todoist tasks).
3. **Auto-Typing:** Uses `evdev` to create a virtual kernel-level keyboard to paste the text directly into any application, bypassing Wayland's security restrictions on simulated keystrokes.

## Prerequisites
- `faster-whisper`
- `evdev` (Python package)
- `wl-clipboard`
- `zenity` (for graphical popups)
- `/dev/uinput` permissions for the user

## Features
- **Smart Dictation:** Trigger and talk. It fixes phonetic Whisper errors and your custom vocabulary.
- **Modes:** Different prompts for different contexts — email, chat, formal, notes, or fully custom.
- **Raw mode (`--mode raw`):** Bypass Gemini entirely, paste Whisper output directly for maximum speed.
- **Vault mode (`--mode vault`):** Save transcription as an Obsidian note instead of pasting. Gemini generates the note title automatically.
- **Append (`--append`):** Add new text to the existing clipboard content instead of replacing it.
- **Auto-stop by silence:** Set `silence_timeout` in config and the recording stops automatically — no second keypress needed.
- **Auto-stop by VAD (smoothed):** Set `auto_stop: "vad"` — voice-activity detection with onset/hangover/prefill (`vad.py`), so it doesn't cut on brief silences or start on noise. Uses energy fallback, or silero-vad if installed (`vad_use_silero: true`).
- **Blank transcription filter:** silently discards empty/noise transcriptions instead of pasting garbage.
- **Editable post-process prompts:** `--prompts` lists them; edit `post_process` in config (multi-prompt, selectable via `selected_prompt`).
- **Configurable LLM provider:** `llm_provider.provider: "openai"` for OpenAI-compatible endpoints (with optional `lang_detect`), or Gemini by default.
- **Recording overlay:** `overlay: "tray" | "overlay"` state indicator while recording (`overlay.py`).
- **Push-to-talk (`ptt.py`):** Hold a key to record, release to transcribe. Runs as a background daemon.
- **History (`--history [N]`):** Browse the last N transcriptions from the terminal.
- **Custom Vocabulary:** Define your own terms (names, acronyms, brands) in the config file.
- **Multi-language:** Set any language supported by Whisper in the config file.
- **Project Navigation:** *"Abre el proyecto CreaEfecto"*
- **Obsidian Vault:** *"Abre el vault"*
- **RAG Search:** *"Busca en mis notas sobre TheIA"* (Uses the `ask_sources` RAG pipeline across Obsidian vault + personal documents, shows a Zenity popup with the AI's answer).
- **Todoist Tasks:** *"Recuérdame llamar a cliente mañana"*
- **OpenClaw (Sherlock):** *"Dile a Sherlock que revise mi correo"*

## Configuration

On first run, a config file is created at `~/.config/theia_dictate/config.json`:

```json
{
  "language": "es",
  "default_mode": "default",
  "vocabulary": ["TheIA", "PLAI", "CreaEfecto"],
  "modes": {
    "default": { "name": "Default", "prompt": "" },
    "email":   { "name": "Email",   "prompt": "Formatea el texto como un email profesional..." },
    "chat":    { "name": "Chat",    "prompt": "Tono casual y breve, como un mensaje de WhatsApp..." },
    "formal":  { "name": "Formal",  "prompt": "Lenguaje técnico y profesional..." },
    "notes":   { "name": "Notas",   "prompt": "Formatea como bullet points..." }
  }
}
```

You can add your own modes or edit the prompts freely. Use `--mode <name>` to activate a specific mode, or set `default_mode` in the config.

The config file is created automatically with defaults on the first run of `theia-dictate`.

## Architecture

```
First press  ──► arecord starts          ──► preloader.py (background)
                  audio captured              │
                                              ├─ loads WhisperModel (~3.7s)
                                              └─ waits for READY signal

Second press ──► arecord stops           ──► signals preloader via READY_FILE
                                              │
                                              ├─ transcribes audio (~5.4s, CPU)
                                              └─ writes result to RESULT_FILE
                  reads transcription ◄──────┘
                  calls Gemini Flash (intent analysis + refinement)
                  executes command or pastes text
```

Total latency from second press to paste: ~10s on CPU (vs ~14s without pre-loader).

## Usage
Trigger the script via a custom keyboard shortcut (e.g., `Alt+Z`).
- First press: Starts recording (🔴) and pre-loads the Whisper model in background.
- Second press: Stops, transcribes, routes, and executes/pastes the text (⏳ -> ✅).

To use a specific mode, pass `--mode` when binding the shortcut:
```bash
theia-dictate --mode email
theia-dictate --mode chat
theia-dictate --mode raw      # fastest: no Gemini call
theia-dictate --mode vault    # saves to Obsidian instead of pasting
theia-dictate --append        # appends to existing clipboard content
theia-dictate --history       # show last 10 transcriptions
theia-dictate --history 25    # show last 25
```

### Push-to-talk

Run `ptt.py` as a startup daemon. It monitors a key (default: `F9`, configurable via `ptt_key` in the config) and starts/stops recording on press/release:

```bash
python3 ptt.py
python3 ptt.py --key KEY_RIGHTCTRL
```

### Auto-stop by silence

Set `silence_timeout` in the config (seconds). Recording stops automatically after that duration of silence — no second keypress needed. Requires `sox`:

```json
{ "silence_timeout": 2.0 }
```