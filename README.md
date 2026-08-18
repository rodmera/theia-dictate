# TheIA Dictate 🎙️🤖 (v1.3.0)

Dictado inteligente nativo de Linux (Wayland/Hyprland), homologado y superando a las apps de dictado consolidadas (Voxtype, Superwhisper, Wisp): **captura continua con RingBuffer en background (0ms de latencia de inicio / 1.0s pre-roll en RAM)**, **audio feedback sutil (beeps al inicio y fin)**, **integración visual con la barra superior de Omarchy (Quickshell)**, **push-to-talk nativo del compositor**, **auto-stop por VAD**, y **post-proceso inteligente por IA (Gemini 3.7 Flash)**.

---

## ⚡ Novedades en v1.3.0

- **Audio Feedback no bloqueante:** Tono sutil al pulsar la tecla de grabación y tono de confirmación al cortar por voz/silencio (`pw-play` en hilo desacoplado con 0 ms de overhead).
- **Integración con la barra de Omarchy / Quickshell:** Soporte para `status --follow --extended --format json` y wrapper `~/.local/bin/omarchy-voxtype-status` que actualiza el indicador visual de micrófono en tiempo real (`Dictation.qml`).
- **Arquitectura RingRecorder (Latencia 0 ms):** Stream de `arecord` permanente en background con búfer circular de 1,0s en RAM. Elimina el delay en frío de ALSA (1,55s) y asegura que nunca se pierda la primera palabra (*"Hola..."*).
- **Aislamiento de modificadores:** Liberación forzada de teclas modificadoras antes de pegar para evitar colisiones con atajos globales del compositor (`SUPER + CTRL + SHIFT + V`).
- **Logs estructurados con milisegundos:** Traza enriquecida con marcas de tiempo en milisegundos, PIDs y nombres de hilo en `/tmp/theia-dictate-debug.log`.

---

## 🚀 Proveedores STT Soportados

1. **Google Gemini (Recomendado / Default):** Modelo `gemini-3.7-flash` vía Google AI Studio. Transcripción directa en ~1,2s sin demoras de cuota.
2. **Google Cloud STT (Chirp 3):** Vía Service Account Key (`~/.config/openclaw-secrets/chirp-sa-key.json`, inmune a expiración RAPT).
3. **Local (Offline):** `faster-whisper` ejecutándose en GPU/CPU local.

---

## ⌨️ Atajos de Teclado (Hyprland)

Configurados en `~/.config/hypr/bindings.lua`:

| Atajo | Acción | Modo |
|---|---|---|
| `F9` | **Push-to-Talk (estilo Voxtype)** | Mantener presionado mientras hablas, soltar para transcribir y pegar. |
| `SUPER + CTRL + G` | **Toggle Manos Libres** | Presionar una vez para iniciar grabación, presionar de nuevo o dejar que el VAD corte por silencio. |
| `SUPER + CTRL + SHIFT + V` | **Modo Vault** | Graba y guarda directamente como nota formateada en Obsidian. |
| `SUPER + CTRL + ESCAPE` | **Cancelar** | Descarta la grabación activa sin transcribir. |

---

## ⚙️ Configuración (`~/.config/theia-dictate/config.json`)

```json
{
  "language": "es",
  "default_mode": "default",
  "stt_provider": "gemini",
  "auto_stop": "vad",
  "vad_use_silero": true,
  "vad_max_silence_ms": 1500,
  "audio_feedback": {
    "enabled": true,
    "volume": 0.5,
    "theme": "subtle"
  },
  "vocabulary": ["TheIA", "PLAI", "CreaEfecto", "Sherlock", "Watson", "Entel", "OpenClaw"]
}
```

---

## 🔍 Troubleshooting y Diagnóstico

### 1. Auditoría del Sistema
```bash
theia-dictate doctor
```

### 2. Inspección de Logs en Vivo
```bash
tail -f /tmp/theia-dictate-debug.log
```

---

## 🧪 Pruebas Automatizadas

Ejecutar la suite completa de 38 tests unitarios e integración:
```bash
python3 tests.py
```
