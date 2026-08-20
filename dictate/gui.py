"""Interfaz gráfica nativa GTK4 para TheIA Notes (Alternativa Granola para Linux/Omarchy).

Permite grabar reuniones (micrófono + salida de PC), tomar apuntes manuales en vivo,
generar minutas estructuradas con Gemini 3.7 Flash y exportarlas a Obsidian Vault.
"""
from datetime import datetime
import os
import subprocess
import sys
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk

from dictate.notes import StructuredNote, render_note_markdown
from dictate.session import NotesSessionManager


class TheIANotesWindow(Gtk.ApplicationWindow):
    """Ventana principal de la aplicación TheIA Notes."""

    def __init__(self, app: Gtk.Application):
        super().__init__(application=app, title="TheIA Notes — Minutas con IA")
        self.set_default_size(900, 750)

        self.session_mgr = NotesSessionManager()
        self.timer_seconds = 0
        self.timer_source_id: int | None = None
        self.current_note: StructuredNote | None = None

        self._build_ui()
        self._load_device_info()

    def _build_ui(self) -> None:
        # Contenedor principal con scroll vertical
        main_scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        main_box.set_margin_start(18)
        main_box.set_margin_end(18)
        main_box.set_margin_top(16)
        main_box.set_margin_bottom(16)
        main_scroller.set_child(main_box)
        self.set_child(main_scroller)

        # 1. Header Bar
        header = Gtk.HeaderBar()
        self.set_titlebar(header)

        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        lbl_app = Gtk.Label(label="<b>TheIA Notes</b>", use_markup=True)
        self.lbl_header_status = Gtk.Label(label="Listo para grabar")
        self.lbl_header_status.set_css_classes(["dim-label"])
        title_box.append(lbl_app)
        title_box.append(self.lbl_header_status)
        header.set_title_widget(title_box)

        # 2. Barra de selección de fuente de audio
        source_frame = Gtk.Frame()
        source_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        source_box.set_margin_start(12)
        source_box.set_margin_end(12)
        source_box.set_margin_top(10)
        source_box.set_margin_bottom(10)
        source_frame.set_child(source_box)
        main_box.append(source_frame)

        source_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        lbl_source_title = Gtk.Label(label="<b>Fuente de audio:</b>", use_markup=True)
        source_row.append(lbl_source_title)

        self.source_dropdown = Gtk.DropDown.new_from_strings([
            "🎙️ + 🔊 Reunión (Brave / Meet / Teams)",
            "🔊 Audio del PC / Videos (Monitor)",
            "🎙️ Micrófono (Solo mi voz)",
        ])
        self.source_dropdown.set_selected(0)
        self.source_dropdown.set_hexpand(True)
        source_row.append(self.source_dropdown)

        btn_audio_settings = Gtk.Button(label="Cambiar dispositivos...")
        btn_audio_settings.connect("clicked", self._on_open_audio_settings)
        source_row.append(btn_audio_settings)
        source_box.append(source_row)

        self.lbl_devices = Gtk.Label(label="Detectando dispositivos de audio PipeWire...")
        self.lbl_devices.set_halign(Gtk.Align.START)
        self.lbl_devices.set_css_classes(["dim-label"])
        source_box.append(self.lbl_devices)

        # 3. Panel de control de sesión y cronómetro
        controls_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        main_box.append(controls_box)

        self.btn_start = Gtk.Button(label="● Iniciar Grabación")
        self.btn_start.set_css_classes(["suggested-action", "pill-button"])
        self.btn_start.connect("clicked", self._on_start_clicked)
        controls_box.append(self.btn_start)

        self.btn_stop = Gtk.Button(label="⏹ Detener y Generar Notas IA")
        self.btn_stop.set_css_classes(["destructive-action", "pill-button"])
        self.btn_stop.set_sensitive(False)
        self.btn_stop.connect("clicked", self._on_stop_clicked)
        controls_box.append(self.btn_stop)

        self.btn_cancel = Gtk.Button(label="✖ Cancelar")
        self.btn_cancel.set_sensitive(False)
        self.btn_cancel.connect("clicked", self._on_cancel_clicked)
        controls_box.append(self.btn_cancel)

        self.lbl_timer = Gtk.Label(label="00:00")
        self.lbl_timer.set_hexpand(True)
        self.lbl_timer.set_halign(Gtk.Align.END)
        self.lbl_timer.set_css_classes(["title-1", "numeric"])
        controls_box.append(self.lbl_timer)

        # 4. Bloc de notas manual en vivo
        notes_frame = Gtk.Frame()
        notes_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        notes_box.set_margin_start(12)
        notes_box.set_margin_end(12)
        notes_box.set_margin_top(10)
        notes_box.set_margin_bottom(10)
        notes_frame.set_child(notes_box)
        main_box.append(notes_frame)

        lbl_manual = Gtk.Label(
            label="<b>📝 Apuntes manuales en vivo</b> (prioridad contextual para Gemini 3.7 Flash):",
            use_markup=True,
        )
        lbl_manual.set_halign(Gtk.Align.START)
        notes_box.append(lbl_manual)

        manual_scroller = Gtk.ScrolledWindow(min_content_height=110)
        self.txt_manual_notes = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD)
        self.txt_manual_notes.get_buffer().set_text("")
        manual_scroller.set_child(self.txt_manual_notes)
        notes_box.append(manual_scroller)

        # 5. Editor y visualizador de nota generada
        editor_frame = Gtk.Frame()
        self.editor_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.editor_box.set_margin_start(12)
        self.editor_box.set_margin_end(12)
        self.editor_box.set_margin_top(10)
        self.editor_box.set_margin_bottom(10)
        editor_frame.set_child(self.editor_box)
        main_box.append(editor_frame)

        lbl_editor_title = Gtk.Label(
            label="<b>✨ Minuta de Reunión Generada</b> (Revisar y editar antes de guardar):",
            use_markup=True,
        )
        lbl_editor_title.set_halign(Gtk.Align.START)
        self.editor_box.append(lbl_editor_title)

        # Título y tags
        meta_grid = Gtk.Grid(column_spacing=10, row_spacing=8)
        self.editor_box.append(meta_grid)

        meta_grid.attach(Gtk.Label(label="Título:", halign=Gtk.Align.START), 0, 0, 1, 1)
        self.entry_title = Gtk.Entry(placeholder_text="Título de la reunión...")
        self.entry_title.set_hexpand(True)
        meta_grid.attach(self.entry_title, 1, 0, 1, 1)

        meta_grid.attach(Gtk.Label(label="Tags:", halign=Gtk.Align.START), 0, 1, 1, 1)
        self.entry_tags = Gtk.Entry(text="unique, voice-note, reunion")
        self.entry_tags.set_hexpand(True)
        meta_grid.attach(self.entry_tags, 1, 1, 1, 1)

        # Secciones de la nota
        notebook = Gtk.Notebook()
        self.editor_box.append(notebook)

        # Tab 1: Resumen
        self.txt_summary = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD)
        sc1 = Gtk.ScrolledWindow(min_content_height=140)
        sc1.set_child(self.txt_summary)
        notebook.append_page(sc1, Gtk.Label(label="Resumen"))

        # Tab 2: Puntos Clave
        self.txt_key_points = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD)
        sc2 = Gtk.ScrolledWindow(min_content_height=140)
        sc2.set_child(self.txt_key_points)
        notebook.append_page(sc2, Gtk.Label(label="Puntos Clave"))

        # Tab 3: Decisiones
        self.txt_decisions = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD)
        sc3 = Gtk.ScrolledWindow(min_content_height=140)
        sc3.set_child(self.txt_decisions)
        notebook.append_page(sc3, Gtk.Label(label="Decisiones"))

        # Tab 4: Compromisos y Tareas
        self.txt_action_items = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD)
        sc4 = Gtk.ScrolledWindow(min_content_height=140)
        sc4.set_child(self.txt_action_items)
        notebook.append_page(sc4, Gtk.Label(label="Tareas / Action Items"))

        # Tab 5: Transcripción Textual
        self.txt_transcript = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD)
        sc5 = Gtk.ScrolledWindow(min_content_height=140)
        sc5.set_child(self.txt_transcript)
        notebook.append_page(sc5, Gtk.Label(label="Transcripción"))

        # Barra de acciones finales
        action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.editor_box.append(action_row)

        self.btn_save_vault = Gtk.Button(label="💾 Guardar en Obsidian Vault")
        self.btn_save_vault.set_css_classes(["suggested-action"])
        self.btn_save_vault.connect("clicked", self._on_save_vault_clicked)
        action_row.append(self.btn_save_vault)

        self.btn_copy = Gtk.Button(label="📋 Copiar Markdown")
        self.btn_copy.connect("clicked", self._on_copy_markdown_clicked)
        action_row.append(self.btn_copy)

        self.btn_discard = Gtk.Button(label="🗑️ Descartar")
        self.btn_discard.connect("clicked", self._on_discard_clicked)
        action_row.append(self.btn_discard)

        # 6. Status label inferior
        self.lbl_status = Gtk.Label(label="")
        self.lbl_status.set_halign(Gtk.Align.START)
        self.lbl_status.set_css_classes(["dim-label"])
        main_box.append(self.lbl_status)

    def _load_device_info(self) -> None:
        devices = self.session_mgr.get_devices_info()
        src = devices.get("default_source", "Micrófono")
        snk = devices.get("default_sink", "Altavoces")
        self.lbl_devices.set_text(f"🎙️ Entrada: {src}  |  🔊 Salida: {snk}")

    def _get_selected_source_key(self) -> str:
        idx = self.source_dropdown.get_selected()
        if idx == 0:
            return "meeting"
        elif idx == 1:
            return "monitor"
        return "mic"

    def _on_open_audio_settings(self, _btn: Gtk.Button) -> None:
        # Abrir control de volumen / dispositivos de Omarchy o pavucontrol
        for cmd in [["pavucontrol"], ["omarchy-settings", "audio"], ["helvum"], ["qpwgraph"]]:
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            except FileNotFoundError:
                continue
        self.lbl_status.set_text("No se encontró pavucontrol instalado.")

    def _on_start_clicked(self, _btn: Gtk.Button) -> None:
        source_key = self._get_selected_source_key()
        started = self.session_mgr.start_recording(source=source_key)
        if not started:
            self.lbl_status.set_text("Error al iniciar grabación.")
            return

        self.btn_start.set_sensitive(False)
        self.btn_stop.set_sensitive(True)
        self.btn_cancel.set_sensitive(True)
        self.source_dropdown.set_sensitive(False)

        self.lbl_header_status.set_text("🔴 Grabando sesión...")
        self.lbl_status.set_text("Grabando audio con PipeWire...")

        self.timer_seconds = 0
        self.lbl_timer.set_text("00:00")
        self.timer_source_id = GLib.timeout_add_seconds(1, self._on_timer_tick)

    def _on_timer_tick(self) -> bool:
        self.timer_seconds += 1
        mins = self.timer_seconds // 60
        secs = self.timer_seconds % 60
        self.lbl_timer.set_text(f"{mins:02d}:{secs:02d}")
        return True

    def _stop_timer(self) -> None:
        if self.timer_source_id:
            GLib.source_remove(self.timer_source_id)
            self.timer_source_id = None

    def _on_stop_clicked(self, _btn: Gtk.Button) -> None:
        self._stop_timer()
        self.btn_stop.set_sensitive(False)
        self.btn_cancel.set_sensitive(False)

        self.lbl_header_status.set_text("⏳ Procesando con Gemini 3.7 Flash...")
        self.lbl_status.set_text("Transcribiendo y estructurando minuta...")

        buf = self.txt_manual_notes.get_buffer()
        start, end = buf.get_bounds()
        manual_text = buf.get_text(start, end, True).strip()

        # Procesar de forma asíncrona sin congelar la GUI
        self.session_mgr.stop_and_process(
            manual_notes=manual_text,
            on_complete=lambda note: GLib.idle_add(self._on_processing_complete, note),
            on_error=lambda err: GLib.idle_add(self._on_processing_error, err),
        )

    def _on_processing_complete(self, note: StructuredNote) -> None:
        self.current_note = note
        self.btn_start.set_sensitive(True)
        self.source_dropdown.set_sensitive(True)

        self.lbl_header_status.set_text("✅ Minuta lista para revisión")
        self.lbl_status.set_text("Minuta generada con éxito. Revisa y haz clic en Guardar.")

        # Poblar campos del editor
        self.entry_title.set_text(note.title)
        self.entry_tags.set_text(", ".join(note.tags))
        self.txt_summary.get_buffer().set_text(note.summary)
        self.txt_key_points.get_buffer().set_text("\n".join(f"- {p}" for p in note.key_points))
        self.txt_decisions.get_buffer().set_text("\n".join(f"- {d}" for d in note.decisions))

        action_lines = []
        for a in note.action_items:
            t = a.get("task", "")
            o = a.get("owner", "")
            d = a.get("due", "")
            meta = f" (Responsable: {o} | Plazo: {d})" if (o or d) else ""
            action_lines.append(f"- [ ] **{t}**{meta}")
        self.txt_action_items.get_buffer().set_text("\n".join(action_lines))
        self.txt_transcript.get_buffer().set_text(note.raw_transcript)

    def _on_processing_error(self, error_msg: str) -> None:
        self.btn_start.set_sensitive(True)
        self.source_dropdown.set_sensitive(True)
        self.lbl_header_status.set_text("❌ Error de procesamiento")
        self.lbl_status.set_text(f"Error: {error_msg}")

    def _on_cancel_clicked(self, _btn: Gtk.Button) -> None:
        self._stop_timer()
        self.session_mgr.cancel_session()
        self.btn_start.set_sensitive(True)
        self.btn_stop.set_sensitive(False)
        self.btn_cancel.set_sensitive(False)
        self.source_dropdown.set_sensitive(True)
        self.lbl_timer.set_text("00:00")
        self.lbl_header_status.set_text("Sesión cancelada")
        self.lbl_status.set_text("Grabación cancelada y descartada.")

    def _collect_edited_note(self) -> StructuredNote:
        """Extrae la nota editada por el usuario desde los widgets de la GUI."""
        title = self.entry_title.get_text().strip()
        tags_raw = self.entry_tags.get_text().strip()
        tags = [t.strip() for t in tags_raw.split(",") if t.strip()]

        def get_buf_text(tv: Gtk.TextView) -> str:
            buf = tv.get_buffer()
            s, e = buf.get_bounds()
            return buf.get_text(s, e, True).strip()

        summary = get_buf_text(self.txt_summary)
        key_points = [l.strip("- ") for l in get_buf_text(self.txt_key_points).splitlines() if l.strip()]
        decisions = [l.strip("- ") for l in get_buf_text(self.txt_decisions).splitlines() if l.strip()]

        action_items = []
        for line in get_buf_text(self.txt_action_items).splitlines():
            line_clean = line.strip()
            if not line_clean:
                continue
            line_clean = line_clean.removeprefix("- [ ]").removeprefix("- [x]").strip()
            action_items.append({"task": line_clean})

        raw_transcript = get_buf_text(self.txt_transcript)

        now = self.current_note.created_at if self.current_note else datetime.now()
        topic = self.current_note.topic if self.current_note else "reunion"

        return StructuredNote(
            title=title,
            created_at=now,
            summary=summary,
            topic=topic,
            key_points=key_points,
            decisions=decisions,
            action_items=action_items,
            raw_transcript=raw_transcript,
            tags=tags,
        )

    def _on_save_vault_clicked(self, _btn: Gtk.Button) -> None:
        note_to_save = self._collect_edited_note()
        if not note_to_save.title:
            note_to_save.title = f"Minuta {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            self.entry_title.set_text(note_to_save.title)

        res = self.session_mgr.save_note_to_vault(note_to_save)
        if res.get("status") == "ok":
            self.btn_save_vault.set_sensitive(False)
            self.lbl_status.set_text(f"✅ Guardado en Obsidian Vault: {res.get('title')}")
            self.lbl_header_status.set_text("✅ Guardado en Vault")
        else:
            self.lbl_status.set_text(f"❌ Error al guardar en Vault: {res.get('error')}")

    def _on_copy_markdown_clicked(self, _btn: Gtk.Button) -> None:
        note = self._collect_edited_note()
        md = render_note_markdown(note)
        clipboard = self.get_display().get_clipboard()
        clipboard.set(md)
        self.lbl_status.set_text("📋 Markdown copiado al portapapeles.")

    def _on_discard_clicked(self, _btn: Gtk.Button) -> None:
        """Descarta la sesión y limpia todos los campos del editor y notas manuales."""
        self.session_mgr.discard_session()
        self.current_note = None

        # Limpiar notas manuales y campos del editor
        self.txt_manual_notes.get_buffer().set_text("")
        self.entry_title.set_text("")
        self.entry_tags.set_text("unique, voice-note, reunion")
        self.txt_summary.get_buffer().set_text("")
        self.txt_key_points.get_buffer().set_text("")
        self.txt_decisions.get_buffer().set_text("")
        self.txt_action_items.get_buffer().set_text("")
        self.txt_transcript.get_buffer().set_text("")

        # Desactivar botones de acción hasta nueva generación
        self.btn_save_vault.set_sensitive(False)
        self.btn_copy.set_sensitive(False)
        self.btn_discard.set_sensitive(False)

        self.lbl_timer.set_text("00:00")
        self.lbl_header_status.set_text("Listo para grabar")
        self.lbl_status.set_text("Nota descartada. Lienzo limpio listo para una nueva sesión.")


class TheIANotesApp(Gtk.Application):
    """Aplicación principal de escritorio TheIA Notes."""

    def __init__(self):
        super().__init__(application_id="cl.theia.notes")

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = TheIANotesWindow(self)
        win.present()


def main():
    """Punto de entrada de la aplicación GUI TheIA Notes."""
    app = TheIANotesApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
