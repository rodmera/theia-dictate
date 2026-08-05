# Contributing to TheIA Dictate

Welcome! We are excited to have you contribute to TheIA Dictate. This project aims to bring a premium, AI-powered dictation and system automation experience natively to Linux (Wayland).

## 🚀 How to Contribute

There are several ways to get involved:

### 1. Adding New Voice Commands (Intents)
TheIA Dictate uses Gemini 3 Flash to route user intents. If you want to add a new voice command (e.g., "Translate this", "Play music"):
- Open `theia-dictate`.
- Locate the JSON schema inside the `stop_and_transcribe()` function.
- Add your new command to the `command_type` description.
- Add the corresponding `elif cmd == "your_command":` block below to handle the execution.

### 2. Improving Wayland Integration
Currently, we use `evdev` to simulate hardware keystrokes and bypass Wayland security protocols. If you know better ways to interact with `wlroots` or handle focus stealing (like our `start_new_session=True` workaround), PRs are welcome!

### 3. Testing and Bug Reports
Since Linux environments vary wildly (GNOME, KDE, Hyprland), please open an Issue if the clipboard injection (`wl-copy`) or virtual keyboard fails on your setup, detailing your Desktop Environment and audio server (PipeWire/PulseAudio).

## 🛠️ Development Setup

1. Clone the repo.
2. Ensure you have the required dependencies:
   ```bash
   sudo apt install wl-clipboard zenity python3-evdev
   ```
3. Create a local `.env` file or export your Gemini API key:
   ```bash
   export GEMINI_API_KEY="your_api_key_here"
   ```
4. Run the script directly from your terminal to see the `print` outputs and debug:
   ```bash
   ./theia-dictate
   ```

## 📝 Pull Request Process
1. Fork the repo and create your branch from `master`.
2. Keep your PRs focused on a single feature or bugfix.
3. Test the transcription pipeline using both passive dictation and active commands before submitting.
