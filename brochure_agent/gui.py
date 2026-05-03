from __future__ import annotations

import queue
import threading
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter as tk

import customtkinter as ctk

from .models import AppConfig, DEFAULT_MODEL
from .pipeline import run_pipeline, validate_config


class BrochureGui:
    def __init__(self, root: ctk.CTk, default_template: Path, default_model: str = DEFAULT_MODEL) -> None:
        self.root = root
        self.root.title("Real Estate Brochure Generator")
        self.root.geometry("940x680")
        self.root.minsize(820, 600)

        self.pdf_var = tk.StringVar()
        self.images_var = tk.StringVar()
        self.template_var = tk.StringVar(value=str(default_template))
        self.output_var = tk.StringVar(value=str(Path.cwd() / "brochure_output.pptx"))
        self.model_var = tk.StringVar(value=default_model)
        self.status_var = tk.StringVar(value="Ready")

        self.log_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.generate_button: ctk.CTkButton | None = None
        self.open_output_button: ctk.CTkButton | None = None
        self.progress_bar: ctk.CTkProgressBar | None = None
        self.log_widget: ctk.CTkTextbox | None = None

        self._build()
        self.root.after(100, self._drain_log_queue)

    def _build(self) -> None:
        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        main = ctk.CTkFrame(self.root, corner_radius=0, fg_color="transparent")
        main.grid(row=0, column=0, sticky="nsew", padx=22, pady=22)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)

        header = ctk.CTkFrame(main, corner_radius=18)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 16))
        header.columnconfigure(0, weight=1)

        title = ctk.CTkLabel(header, text="Real Estate Brochure Generator", font=ctk.CTkFont(size=26, weight="bold"))
        title.grid(row=0, column=0, sticky="w", padx=20, pady=(18, 4))

        subtitle = ctk.CTkLabel(
            header,
            text="Choose a listing PDF, image folder, template, and output location. The app handles extraction, image selection, and PowerPoint rendering.",
            anchor="w",
            justify="left",
        )
        subtitle.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 18))

        body = ctk.CTkFrame(main, corner_radius=18)
        body.grid(row=1, column=0, sticky="ew", pady=(0, 16))
        body.columnconfigure(1, weight=1)

        self._path_row(body, 0, "Listing PDF", "The PDF or saved listing email.", self.pdf_var, self._select_pdf)
        self._path_row(body, 1, "Image Folder", "Folder containing listing photos.", self.images_var, self._select_image_folder)
        self._path_row(body, 2, "Template", "PowerPoint template with placeholders.", self.template_var, self._select_template)
        self._path_row(body, 3, "Save As", "Where the generated brochure will be saved.", self.output_var, self._select_output)

        advanced = ctk.CTkFrame(body, fg_color="transparent")
        advanced.grid(row=4, column=0, columnspan=3, sticky="ew", padx=18, pady=(4, 18))
        advanced.columnconfigure(1, weight=1)
        ctk.CTkLabel(advanced, text="Model", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, sticky="w", padx=(0, 12))
        ctk.CTkEntry(advanced, textvariable=self.model_var, height=34).grid(row=0, column=1, sticky="ew")

        actions = ctk.CTkFrame(main, corner_radius=18)
        actions.grid(row=2, column=0, sticky="nsew")
        actions.columnconfigure(0, weight=1)
        actions.rowconfigure(2, weight=1)

        top_actions = ctk.CTkFrame(actions, fg_color="transparent")
        top_actions.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 10))
        top_actions.columnconfigure(2, weight=1)

        self.generate_button = ctk.CTkButton(top_actions, text="Generate Brochure", height=42, command=self.start_generation)
        self.generate_button.grid(row=0, column=0, sticky="w")

        self.open_output_button = ctk.CTkButton(top_actions, text="Open Output Folder", height=42, command=self.open_output_folder, state="disabled")
        self.open_output_button.grid(row=0, column=1, sticky="w", padx=(10, 0))

        status = ctk.CTkLabel(top_actions, textvariable=self.status_var, anchor="e")
        status.grid(row=0, column=2, sticky="e")

        self.progress_bar = ctk.CTkProgressBar(actions, mode="indeterminate")
        self.progress_bar.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 12))
        self.progress_bar.set(0)

        self.log_widget = ctk.CTkTextbox(actions, wrap="word", state="disabled")
        self.log_widget.grid(row=2, column=0, sticky="nsew", padx=18, pady=(0, 18))

    def _path_row(self, parent: ctk.CTkFrame, row: int, label: str, help_text: str, variable: tk.StringVar, command) -> None:
        label_frame = ctk.CTkFrame(parent, fg_color="transparent")
        label_frame.grid(row=row, column=0, sticky="w", padx=(18, 10), pady=10)

        ctk.CTkLabel(label_frame, text=label, font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(label_frame, text=help_text, text_color=("gray40", "gray70")).grid(row=1, column=0, sticky="w")

        ctk.CTkEntry(parent, textvariable=variable, height=38).grid(row=row, column=1, sticky="ew", padx=(0, 10), pady=10)
        ctk.CTkButton(parent, text="Browse", width=110, height=38, command=command).grid(row=row, column=2, sticky="e", padx=(0, 18), pady=10)

    def _select_pdf(self) -> None:
        path = filedialog.askopenfilename(title="Select listing PDF", filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")])
        if path:
            self.pdf_var.set(path)
            self._suggest_output_path(Path(path))

    def _select_image_folder(self) -> None:
        path = filedialog.askdirectory(title="Select image folder")
        if path:
            self.images_var.set(path)

    def _select_template(self) -> None:
        path = filedialog.askopenfilename(title="Select PowerPoint template", filetypes=[("PowerPoint files", "*.pptx"), ("All files", "*.*")])
        if path:
            self.template_var.set(path)

    def _select_output(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Save brochure as",
            defaultextension=".pptx",
            filetypes=[("PowerPoint files", "*.pptx"), ("All files", "*.*")],
            initialfile=Path(self.output_var.get()).name or "brochure_output.pptx",
        )
        if path:
            self.output_var.set(path)

    def _suggest_output_path(self, pdf_path: Path) -> None:
        if self.output_var.get() and Path(self.output_var.get()).name != "brochure_output.pptx":
            return
        safe_name = pdf_path.stem.replace(" ", "_")
        self.output_var.set(str(pdf_path.with_name(f"{safe_name}_brochure.pptx")))

    def start_generation(self) -> None:
        if self.worker and self.worker.is_alive():
            return

        config = AppConfig(
            pdf_path=Path(self.pdf_var.get().strip()),
            image_dir=Path(self.images_var.get().strip()),
            template_path=Path(self.template_var.get().strip()),
            output_path=Path(self.output_var.get().strip()),
            model=self.model_var.get().strip() or DEFAULT_MODEL,
        )

        try:
            validate_config(config)
        except Exception as exc:
            messagebox.showerror("Check inputs", str(exc))
            return

        self._clear_log()
        self._set_running(True)
        self._append_log("Starting brochure generation...")

        self.worker = threading.Thread(target=self._run_generation, args=(config,), daemon=True)
        self.worker.start()

    def _run_generation(self, config: AppConfig) -> None:
        try:
            output_path = run_pipeline(config, logger=self._queue_info)
        except Exception as exc:
            self.log_queue.put(("error", f"{exc}\n\n{traceback.format_exc()}"))
            return
        self.log_queue.put(("done", str(output_path)))

    def _queue_info(self, message: str) -> None:
        self.log_queue.put(("info", message))

    def _drain_log_queue(self) -> None:
        try:
            while True:
                kind, message = self.log_queue.get_nowait()
                if kind == "info":
                    self._append_log(message)
                    self.status_var.set(message)
                elif kind == "done":
                    self._append_log(f"Finished: {message}")
                    self._set_running(False)
                    self.status_var.set("Finished")
                    if self.open_output_button:
                        self.open_output_button.configure(state="normal")
                    messagebox.showinfo("Brochure created", f"Saved brochure to:\n{message}")
                elif kind == "error":
                    self._append_log("ERROR:")
                    self._append_log(message)
                    self._set_running(False)
                    self.status_var.set("Failed")
                    messagebox.showerror("Generation failed", message.splitlines()[0])
        except queue.Empty:
            pass
        self.root.after(100, self._drain_log_queue)

    def _set_running(self, running: bool) -> None:
        if self.generate_button:
            self.generate_button.configure(state="disabled" if running else "normal")
        if self.open_output_button:
            self.open_output_button.configure(state="disabled")
        if self.progress_bar:
            if running:
                self.progress_bar.start()
            else:
                self.progress_bar.stop()
                self.progress_bar.set(0)
        self.status_var.set("Running..." if running else "Ready")

    def _append_log(self, message: str) -> None:
        if not self.log_widget:
            return
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", message + "\n")
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")

    def _clear_log(self) -> None:
        if not self.log_widget:
            return
        self.log_widget.configure(state="normal")
        self.log_widget.delete("1.0", "end")
        self.log_widget.configure(state="disabled")

    def open_output_folder(self) -> None:
        output_path = Path(self.output_var.get().strip())
        folder = output_path.parent if output_path.parent.exists() else Path.cwd()
        open_folder(folder)


def open_folder(folder: Path) -> None:
    import os
    import subprocess
    import sys

    if sys.platform.startswith("win"):
        os.startfile(folder)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
    else:
        subprocess.Popen(["xdg-open", str(folder)])


def launch_gui(default_template: Path, default_model: str = DEFAULT_MODEL) -> bool:
    root = ctk.CTk()
    BrochureGui(root, default_template=default_template, default_model=default_model)
    root.mainloop()
    return True
