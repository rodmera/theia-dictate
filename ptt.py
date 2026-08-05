#!/usr/bin/env python3
"""Daemon push-to-talk para TheIA Dictate.

Mantén presionada la tecla PTT para grabar, suelta para transcribir.
La tecla se configura en ~/.config/theia-dictate/config.json → "ptt_key".

Uso: python3 ptt.py
     python3 ptt.py --key KEY_F9
"""
import os
import sys
import json
import argparse
import subprocess
import threading

try:
    from evdev import InputDevice, ecodes, list_devices
except ImportError:
    print("evdev no instalado. Ejecuta: pip install evdev")
    sys.exit(1)

CONFIG_FILE  = os.path.expanduser("~/.config/theia-dictate/config.json")
SCRIPT_DIR   = os.path.dirname(os.path.realpath(__file__))
SUPER_DICTATE = os.path.join(SCRIPT_DIR, "theia-dictate")


def load_ptt_key(override=None):
    key_name = override
    if not key_name:
        try:
            with open(CONFIG_FILE) as f:
                cfg = json.load(f)
            key_name = cfg.get("ptt_key", "KEY_F9")
        except Exception:
            key_name = "KEY_F9"
    key_code = getattr(ecodes, key_name, None)
    if key_code is None:
        print(f"Tecla no reconocida: {key_name}. Verifica los nombres en /usr/include/linux/input-event-codes.h")
        sys.exit(1)
    return key_code, key_name


def find_keyboards():
    keyboards = []
    for path in list_devices():
        try:
            dev = InputDevice(path)
            caps = dev.capabilities().get(ecodes.EV_KEY, [])
            if ecodes.KEY_A in caps:
                keyboards.append(dev)
        except Exception:
            pass
    return keyboards


is_recording = False
lock = threading.Lock()


def trigger_theia_dictate():
    subprocess.Popen([sys.executable, SUPER_DICTATE],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def handle_event(event, key_code):
    global is_recording
    if event.type != ecodes.EV_KEY or event.code != key_code:
        return
    with lock:
        if event.value == 1 and not is_recording:   # key down → iniciar
            is_recording = True
            trigger_theia_dictate()
        elif event.value == 0 and is_recording:      # key up → detener
            is_recording = False
            trigger_theia_dictate()


def monitor_device(dev, key_code):
    try:
        dev.grab()
    except Exception:
        pass  # si no se puede grabar, monitorear sin grab
    try:
        for event in dev.read_loop():
            handle_event(event, key_code)
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="TheIA Dictate push-to-talk daemon")
    parser.add_argument("--key", default=None, help="Tecla PTT (ej. KEY_F9, KEY_RIGHTCTRL)")
    args = parser.parse_args()

    key_code, key_name = load_ptt_key(args.key)
    keyboards = find_keyboards()
    if not keyboards:
        print("No se encontraron dispositivos de teclado.")
        sys.exit(1)

    print(f"TheIA Dictate PTT activo — tecla: {key_name} — Ctrl+C para salir.")
    print(f"Monitoreando {len(keyboards)} dispositivo(s).")

    threads = []
    for dev in keyboards:
        t = threading.Thread(target=monitor_device, args=(dev, key_code), daemon=True)
        t.start()
        threads.append(t)

    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("\nPTT detenido.")


if __name__ == '__main__':
    main()
