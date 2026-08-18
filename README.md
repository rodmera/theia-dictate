# TheIA Dictate 🎙️🤖 (v1.2.0)

Dictado inteligente nativo de Linux (Wayland/Hyprland), homologado a los patrones de las apps de dictado consolidadas (Voxtype, Superwhisper, Wisp): **captura continua con RingBuffer en background (0ms de latencia de inicio / 1.0s pre-roll en RAM)**, **push-to-talk nativo del compositor**, **auto-stop por VAD**, **notificaciones de escritorio estándar**, y **post-proceso por IA**.

---

## ⚡ Novedades en v1.2.0

- **Arquitectura RingRecorder (Latencia de inicio 0 ms):** El daemon mantiene el stream de `arecord` abierto permanentemente en background en un hilo dedicado con un búfer circular en RAM (1,0s de *pre-roll*). Elimina el retardo en frío de apertura del dispositivo ALSA/PipeWire (1,55s), garantizando que la primera palabra (*"Hola..."*) nunca se corte.
- **Auto-stop por VAD protegido:** Gestión de estados y callbacks VAD desacoplados del ciclo de vida del daemon.
- **Aislamiento de modificadores en pegado:** Liberación forzada y explícita de modificadores (`SUPER`, `CTRL`, `SHIFT`, `ALT`) antes de inyectar `Ctrl+V` para evitar colisiones con atajos globales de Hyprland/Obsidian.
- **Diagnóstico y Logging Estructurado:** Logs con marcas de tiempo en milisegundos, PIDs y nombres de hilo para troubleshooting instantáneo en `/tmp/theia-dictate-debug.log`.

---

## 🚀 Proveedores STT Soportados

1. **Google Gemini (Recomendado / Default):** Modelo `gemini-3.7-flash` vía Google AI Studio (sin costo). Transcripción directa en ~1,2s.
2. **Google Cloud STT (Chirp 3):** Vía Service Account Key (`~/.config/openclaw-secrets/chirp-sa-key.json`, inmune a expiración RAPT) o credenciales OAuth ADC.
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

## 🔍 Troubleshooting y Diagnóstico

### 1. Auditoría del Sistema
Ejecuta el comando integrado para validar credenciales, providers y estado del daemon:
```bash
theia-dictate doctor
```

### 2. Inspección de Logs en Vivo
La traza estructurada con milisegundos e hilos se registra en:
```bash
tail -f /tmp/theia-dictate-debug.log
```
Formato de traza:
```text
[HH:MM:SS.mmm] [PID:12345:RingRecorderReader] RingRecorder: grabación iniciada con pre-roll (31744 bytes)
[HH:MM:SS.mmm] [PID:12345:MainThread] VAD auto-stop detectado en stream
[HH:MM:SS.mmm] [PID:12345:MainThread] res.transcribe: provider=gemini text='Hola esto es una prueba'
[HH:MM:SS.mmm] [PID:12345:MainThread] insert_text ejecutado (len=25)
```

### 3. Historial de Transcripciones
Las entradas raw y refinadas se guardan en:
`~/.openclaw/workspace/memory/theia-dictate-history.jsonl`

---

## 🧪 Pruebas Automatizadas

Ejecutar la suite completa de 32 tests unitarios e integración:
```bash
python3 tests.py
```
