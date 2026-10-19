"""Native artwork gallery and crop dialog; all device work stays in NativeOperations."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import x6_artwork as art
import x6_images as images
import x6_picture as picture
import x6_toolkit as toolkit
from PIL import Image, ImageTk

BACKGROUND = "#f3f8f6"
INK = "#173b36"
GALLERY_TILE_PADDING_X = 4


class CropDialog:
    """Non-destructive crop state; only Apply transfers packed pixels to the editor."""

    def __init__(self, parent, source: Image.Image, config: dict, apply, options=None, prepare=None):
        self.window = tk.Toplevel(parent)
        self.window.title("Bildausschnitt bearbeiten")
        self.window.transient(parent)
        self.window.grab_set()
        self.window.configure(bg=BACKGROUND)
        self.source, self.config, self.apply = source, config, apply
        self.prepare = prepare or (lambda rgb: art.decode_rgb565(art.encode_rgb565(rgb, "little"), "little"))
        self.rotation = (options or {}).get("rotation", 0)
        self.mode = tk.StringVar(value=(options or {}).get("mode", "cover"))
        self.zoom = tk.DoubleVar(value=(options or {}).get("zoom", 1))
        self.pan_x = tk.DoubleVar(value=(options or {}).get("pan_x", 0))
        self.pan_y = tk.DoubleVar(value=(options or {}).get("pan_y", 0))
        self.preview_id = None
        frame = ttk.Frame(self.window, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Vorschau aus RGB565 · Ziehen verschiebt das Bild", font=("Segoe UI", 12, "bold")).pack(pady=(0, 12))
        self.canvas = tk.Canvas(frame, width=config["width"], height=config["height"], highlightthickness=0)
        self.canvas.pack(side="left", padx=(0, 24))
        controls = ttk.Frame(frame)
        controls.pack(side="left", fill="both")
        for label, mode in (("Bildfüllend zuschneiden", "cover"), ("Einpassen · schwarze Ränder", "contain")):
            ttk.Radiobutton(controls, text=label, variable=self.mode, value=mode, command=self.schedule).pack(anchor="w", pady=4)
        ttk.Button(controls, text="90° nach rechts drehen", command=self.rotate).pack(fill="x", pady=12)
        for label, variable, low, high in (("Zoom", self.zoom, 1, config["max_zoom"]), ("Links / rechts", self.pan_x, -1, 1), ("Oben / unten", self.pan_y, -1, 1)):
            ttk.Label(controls, text=label).pack(anchor="w")
            ttk.Scale(controls, variable=variable, from_=low, to=high, command=lambda _value: self.schedule()).pack(fill="x", pady=(0, 10))
        ttk.Button(controls, text="Ausschnitt zurücksetzen", command=self.reset).pack(fill="x", pady=5)
        ttk.Button(controls, text="Übernehmen", command=self.accept).pack(fill="x", pady=5)
        ttk.Button(controls, text="Abbrechen", command=self.close).pack(fill="x", pady=5)
        self.canvas.bind("<ButtonPress-1>", self.drag_start)
        self.canvas.bind("<B1-Motion>", self.drag)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Escape>", lambda _event: self.close())
        self.refresh()

    def options(self):
        return {"rotation": self.rotation, "mode": self.mode.get(), "zoom": self.zoom.get(),
                "pan_x": self.pan_x.get(), "pan_y": self.pan_y.get()}

    def schedule(self):
        if self.preview_id:
            self.window.after_cancel(self.preview_id)
        self.preview_id = self.window.after(40, self.refresh)

    def refresh(self):
        self.preview_id = None
        self.rgb = picture.render(self.source, **self.options()).tobytes()
        decoded = self.prepare(self.rgb)
        image = Image.frombytes("RGB", (self.config["width"], self.config["height"]), decoded)
        self.photo = ImageTk.PhotoImage(image, master=self.window)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo)

    def rotate(self):
        self.rotation = (self.rotation + 90) % 360
        self.pan_x.set(0)
        self.pan_y.set(0)
        self.schedule()

    def reset(self):
        self.rotation = 0
        self.mode.set("cover")
        self.zoom.set(1)
        self.pan_x.set(0)
        self.pan_y.set(0)
        self.schedule()

    def drag_start(self, event):
        self.drag_origin = (event.x, event.y, self.pan_x.get(), self.pan_y.get())

    def drag(self, event):
        width, height = self.source.size if self.rotation % 180 == 0 else self.source.size[::-1]
        _, _, drawn_w, drawn_h = picture.geometry(width, height, mode=self.mode.get(), zoom=self.zoom.get())
        x, y, pan_x, pan_y = self.drag_origin
        for variable, initial, delta, distance in ((self.pan_x, pan_x, event.x - x, abs(self.config["width"] - drawn_w)),
                                                   (self.pan_y, pan_y, event.y - y, abs(self.config["height"] - drawn_h))):
            if distance > 0:
                variable.set(max(-1, min(1, initial + delta * 2 / distance)))
        self.schedule()

    def accept(self):
        if self.preview_id:
            self.window.after_cancel(self.preview_id)
            self.preview_id = None
        self.refresh()
        self.apply(self.rgb, self.source, self.options())
        self.close()

    def close(self):
        if self.preview_id:
            self.window.after_cancel(self.preview_id)
        self.window.grab_release()
        self.window.destroy()


class ArtworkWindow:
    """A fixed gallery backed by an immutable local snapshot, never a live device."""

    def __init__(self, host):
        self.host, self.model = host, None
        self.config = art.catalog()
        self.sources, self.photos, self.tiles = {}, {}, {}
        self.active, self.dirty = None, False
        self.window = tk.Toplevel(host.root)
        self.window.title("DDMY X6 · Bilder bearbeiten")
        self.window.geometry("1040x780")
        self.window.minsize(960, 720)
        self.window.configure(bg=BACKGROUND)
        self.controls = []
        panel = ttk.Frame(self.window, padding=18)
        panel.pack(fill="both", expand=True)
        heading = ttk.Frame(panel)
        heading.pack(fill="x")
        ttk.Label(heading, text="Bilder bearbeiten", font=("Segoe UI", 19, "bold")).pack(side="left")
        self.button(heading, "Dateien öffnen", self.open_files).pack(side="right", padx=4)
        self.button(heading, "Ordner öffnen", self.open_folder).pack(side="right", padx=4)
        self.device_button = self.button(heading, "Vom Gerät laden", self.load_device)
        self.device_button.pack(side="right", padx=4)
        self.info = tk.StringVar(value="LFI / BREC oder vollständige Sicherung öffnen. Bearbeitung ist offline möglich.")
        ttk.Label(panel, textvariable=self.info, wraplength=960).pack(anchor="w", pady=12)
        self.coupled = tk.BooleanVar(value=False)
        self.coupling = ttk.Checkbutton(panel, text="Startbilder gemeinsam ändern", variable=self.coupled, command=self.toggle_coupling)
        self.coupling.pack(anchor="w")
        body = ttk.Frame(panel)
        body.pack(fill="both", expand=True, pady=12)
        gallery_frame = ttk.Frame(body)
        gallery_frame.pack(side="left", fill="both", expand=True, padx=(0, 18))
        gallery_background = ttk.Style(self.window).lookup("TFrame", "background") or BACKGROUND
        self.gallery_canvas = tk.Canvas(gallery_frame, width=300, bg=gallery_background, highlightthickness=0)
        scroll = ttk.Scrollbar(gallery_frame, orient="vertical", command=self.gallery_canvas.yview)
        self.gallery_canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.gallery_canvas.pack(side="left", fill="both", expand=True)
        self.gallery = ttk.Frame(self.gallery_canvas)
        self.gallery_window = self.gallery_canvas.create_window((0, 0), window=self.gallery, anchor="nw")
        self.gallery_columns = 0
        self.gallery_canvas.bind("<Configure>", self.resize_gallery)
        self.gallery.bind("<Configure>", lambda _event: self.gallery_canvas.configure(scrollregion=self.gallery_canvas.bbox("all")))
        self.gallery_canvas.bind("<MouseWheel>", lambda event: self.gallery_canvas.yview_scroll(-int(event.delta / 120), "units"))
        for index, slot in enumerate(self.config["slots"]):
            button = ttk.Button(self.gallery, text=self.slot_label(slot) + "\nNicht geladen", width=23,
                                command=lambda key=slot["id"]: self.select(key), compound="top")
            button.grid(row=index // 2, column=index % 2, padx=GALLERY_TILE_PADDING_X, pady=6)
            self.tiles[slot["id"]] = button
        detail = ttk.Frame(body)
        detail.pack(side="right", fill="y")
        self.title = tk.StringVar(value="Bildplatz auswählen")
        ttk.Label(detail, textvariable=self.title, font=("Segoe UI", 13, "bold")).pack(anchor="w", pady=(0, 12))
        previews = ttk.Frame(detail)
        previews.pack()
        self.preview_labels = []
        for column, title in enumerate(("Original", "Ergebnis · RGB565")):
            ttk.Label(previews, text=title).grid(row=0, column=column, pady=5)
            label = ttk.Label(previews)
            label.grid(row=1, column=column, padx=5)
            self.preview_labels.append(label)
        actions = ttk.Frame(detail)
        actions.pack(fill="x", pady=14)
        self.replace_button = self.button(actions, "Ersetzen", self.replace)
        self.edit_button = self.button(actions, "Bearbeiten", self.edit)
        self.reset_button = self.button(actions, "Änderung zurücksetzen", self.reset)
        for button in (self.replace_button, self.edit_button, self.reset_button):
            button.pack(side="left", padx=3)
        ttk.Label(detail, text="240 × 320 · RGB565\nPNG, JPEG, WebP · bis 25 MiB / 40 MP\n14 feste Plätze · keine zusätzlichen Bilder", wraplength=480).pack(anchor="w")
        bottom = ttk.Frame(panel)
        bottom.pack(fill="x")
        self.save_button = self.button(bottom, "Geänderte BINs speichern", self.save_bins)
        self.package_button = self.button(bottom, "Firmwarepaket speichern", self.save_package)
        self.flash_button = self.button(bottom, "Änderungen flashen", self.flash)
        for button in (self.save_button, self.package_button, self.flash_button):
            button.pack(side="left", padx=(0, 8))
        self.button(bottom, "Schließen", self.close).pack(side="right")
        ttk.Label(panel, textvariable=host.activity, wraplength=960, foreground=INK).pack(fill="x", pady=(10, 0))
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.update_controls()

    @staticmethod
    def slot_label(slot):
        """Keep the long boot-slot title readable, including when BREC is absent."""
        return "Frühes Startbild\n(beide Bootkopien)" if slot["id"] == "boot-startup" else slot["label"]

    def resize_gallery(self, event):
        """Fill the canvas with as many readable tile columns as its width permits."""
        width = max(1, event.width)
        self.gallery_canvas.itemconfigure(self.gallery_window, width=width)
        if not self.tiles:
            return
        tile_width = max(button.winfo_reqwidth() for button in self.tiles.values()) + 2 * GALLERY_TILE_PADDING_X
        columns = max(1, min(len(self.tiles), width // tile_width))
        if columns == self.gallery_columns:
            return
        for column in range(max(columns, self.gallery_columns)):
            self.gallery.columnconfigure(column, weight=1 if column < columns else 0,
                                         uniform="artwork" if column < columns else "")
        for index, button in enumerate(self.tiles.values()):
            button.grid_configure(row=index // columns, column=index % columns, sticky="nsew")
        self.gallery_columns = columns

    def button(self, parent, text, command):
        button = ttk.Button(parent, text=text, command=command)
        self.controls.append(button)
        return button

    def update_controls(self):
        for button in self.controls:
            button.configure(state="disabled" if self.host.busy else "normal")
        ready = self.model is not None and not self.host.busy
        changed = ready and bool(self.model.edits)
        for button in (self.replace_button, self.edit_button):
            button.configure(state="normal" if ready and self.active else "disabled")
        can_reset = ready and (self.active in self.model.edits or self.model.contour)
        self.reset_button.configure(state="normal" if can_reset else "disabled")
        for button in (self.save_button, self.package_button, self.flash_button):
            button.configure(state="normal" if changed else "disabled")
        if not changed or set(self.model.originals) != set(images.AREA_FILES):
            self.package_button.configure(state="disabled")
        if not self.host.connected:
            self.flash_button.configure(state="disabled")
        self.device_button.configure(state="normal" if self.host.connected and not self.host.busy else "disabled")
        self.coupling.configure(state="normal" if ready and all(key in self.model.ranges for key in self.config["startup_ids"]) else "disabled")
        for key, button in self.tiles.items():
            button.configure(state="normal" if ready and key in self.model.ranges else "disabled")

    def discard_allowed(self):
        return not self.dirty or messagebox.askokcancel("Änderungen verwerfen?", "Die noch nicht gespeicherten Bildänderungen verwerfen?", parent=self.window)

    def offline(self, title, action, complete=None):
        """Run parsing and file writes in the existing worker, without a device lease."""
        result = {}

        def work():
            result["value"] = action()
            return {"message": title + " · fertig"}

        self.host.start(title, work, device=False, on_result=lambda _event: complete(result["value"]) if complete else None)

    def load_selection(self, selection):
        self.offline("Bilder laden", lambda: art.Artwork(selection, self.host.operations.profile), self.loaded)

    def load_sources(self, sources):
        def load():
            staged = images.select_images(sources, toolkit.new_run("artwork-open").directory / "input", self.host.operations.profile)
            return art.Artwork(staged, self.host.operations.profile)
        self.offline("Bilder laden", load, self.loaded)

    def loaded(self, model):
        self.model, self.sources, self.dirty = model, {}, False
        self.active = next(iter(model.ranges))
        self.coupled.set(model.coupled)
        self.refresh()

    def open_files(self):
        if self.host.busy or not self.discard_allowed():
            return
        files = filedialog.askopenfilenames(parent=self.window, title="LFI und BREC gemeinsam oder ein Firmwarepaket öffnen", filetypes=[("X6-Dateien", "*.bin *.zip")])
        if files:
            self.load_sources(tuple(Path(path) for path in files))

    def open_folder(self):
        if self.host.busy or not self.discard_allowed():
            return
        folder = filedialog.askdirectory(parent=self.window, title="Vollständigen Sicherungsordner öffnen")
        if folder:
            self.load_sources((Path(folder),))

    def load_device(self):
        if self.host.busy or not self.discard_allowed():
            return
        destination = filedialog.asksaveasfilename(parent=self.window, title="Unveränderte Gerätesicherung zuerst speichern",
                                                  initialfile="x6-original.x6backup.zip", defaultextension=".x6backup.zip")
        if not destination:
            return
        path = Path(destination)
        if path.exists():
            messagebox.showerror("Neuen Namen wählen", "Die Originalsicherung darf keine vorhandene Datei überschreiben.", parent=self.window)
            return
        self.host.start("Sicherung für den Bildeditor erstellen", lambda: self.host.operations.dump(path),
                        on_result=lambda event: self.load_sources((path,)) if event.get("complete") else None)

    def select(self, key):
        self.active = key
        self.refresh_previews()
        self.update_controls()

    def refresh(self):
        if not self.model:
            return
        areas = " + ".join(area.upper() for area in self.model.originals)
        message = f"{areas} geladen · {len(self.model.edits)} Bildplätze geändert"
        if not all(key in self.model.ranges for key in self.config["startup_ids"]):
            message += " · Nur frühes Startbild verfügbar" if "brec" in self.model.originals else " · Frühes Startbild fehlt (BREC nicht geladen)"
        self.info.set(message)
        for slot in self.config["slots"]:
            key = slot["id"]
            if key in self.model.ranges:
                image = Image.frombytes("RGB", (self.config["width"], self.config["height"]), self.model.rgb(key))
                image.thumbnail((84, 112))
                self.photos[key] = ImageTk.PhotoImage(image, master=self.window)
                label = self.slot_label(slot)
                self.tiles[key].configure(image=self.photos[key], text=label + ("\n● Geändert" if key in self.model.edits else ""))
            else:
                self.tiles[key].configure(image="", text=self.slot_label(slot) + "\nNicht geladen")
        self.refresh_previews()
        self.update_controls()

    def refresh_previews(self):
        if not self.model or not self.active:
            return
        self.title.set(self.model.ranges[self.active].slot["label"])
        self.preview_photos = []
        for original, label in zip((True, False), self.preview_labels):
            image = Image.frombytes("RGB", (self.config["width"], self.config["height"]), self.model.rgb(self.active, original))
            photo = ImageTk.PhotoImage(image, master=self.window)
            label.configure(image=photo)
            self.preview_photos.append(photo)

    def toggle_coupling(self):
        self.model.coupled = self.coupled.get()

    def replace(self):
        path = filedialog.askopenfilename(parent=self.window, title="Ersatzbild wählen", filetypes=[("Bilder", "*.png *.jpg *.jpeg *.webp")])
        if path:
            self.offline("Bild einlesen", lambda: picture.import_picture(Path(path)), self.crop)

    def crop(self, source, options=None):
        key = self.active

        def apply(rgb, original, settings):
            targets = self.model.targets(key)
            self.model.replace(key, rgb)
            for target in targets:
                self.sources[target] = (original, settings)
            self.dirty = bool(self.model.edits)
            self.refresh()

        self.crop_dialog = CropDialog(self.window, source, self.config, apply, options,
                                      prepare=lambda rgb: self.model.preview_rgb(key, rgb))

    def edit(self):
        if self.active in self.sources:
            self.crop(*self.sources[self.active])
        else:
            self.crop(Image.frombytes("RGB", (self.config["width"], self.config["height"]), self.model.rgb(self.active)))

    def reset(self):
        for target in self.model.targets(self.active):
            self.sources.pop(target, None)
        self.model.reset(self.active)
        self.dirty = bool(self.model.edits)
        self.refresh()

    def saved(self, _result):
        self.dirty = False
        self.info.set("Gespeichert und erneut geprüft. Die ursprünglichen Dateien bleiben erhalten.")

    def save_bins(self):
        areas = self.model.changed_areas
        if len(areas) == 1:
            name = filedialog.asksaveasfilename(parent=self.window, title="Bearbeitete Firmware neu speichern", initialfile=f"x6-bilder-{areas[0]}.bin", defaultextension=".bin")
            if not name:
                return
            destinations = {areas[0]: Path(name)}
        else:
            folder = filedialog.askdirectory(parent=self.window, title="Zielordner für BREC und LFI")
            if not folder:
                return
            destinations = {area: Path(folder) / f"x6-bilder-{area}.bin" for area in areas}
        self.offline("Bearbeitete BINs speichern", lambda: self.model.save_bins(destinations, toolkit.new_run("artwork-save").directory / "candidate"), self.saved)

    def save_package(self):
        name = filedialog.asksaveasfilename(parent=self.window, title="Bearbeitetes Firmwarepaket speichern", initialfile="x6-bilder.x6backup.zip", defaultextension=".x6backup.zip")
        if name:
            self.offline("Firmwarepaket speichern", lambda: self.model.save_package(Path(name), toolkit.new_run("artwork-package").directory / "candidate"), self.saved)

    def flash(self):
        labels = [slot["label"] for slot in self.config["slots"] if slot["id"] in self.model.edits]
        self.offline("Geänderte Firmwarebereiche prüfen", lambda: self.model.stage(toolkit.new_run("artwork-flash").directory / "candidate"),
                     lambda selection: self.host.flash(selection, labels))

    def close(self):
        if self.host.busy or not self.discard_allowed():
            return False
        self.window.destroy()
        self.host.artwork_window = None
        return True
