from __future__ import annotations

import argparse
import sys
import threading
import traceback
from pathlib import Path
from typing import Sequence

from .models import AppConfig, DEFAULT_MODEL
from .pipeline import run_pipeline, validate_config


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a real-estate brochure PPTX from a listing PDF and image folder.")
    parser.add_argument("--pdf", type=Path, help="Path to the listing PDF.")
    parser.add_argument("--images", type=Path, help="Path to the image directory.")
    parser.add_argument("--template", type=Path, default=Path("Brochure_Template.pptx"), help="Path to the PPTX template.")
    parser.add_argument("--output", type=Path, default=Path("brochure_output.pptx"), help="Output PPTX path.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="OpenAI model name.")
    parser.add_argument("--cli", action="store_true", help="Force CLI mode even if tkinter is available.")
    return parser.parse_args(argv)


def maybe_launch_gui(default_template: Path, default_model: str) -> bool:
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox
        from tkinter.scrolledtext import ScrolledText
    except Exception:
        return False

    class BrochureApp:
        def __init__(self, root: tk.Tk) -> None:
            self.root = root
            self.root.title("Brochure Agent")
            self.root.geometry("760x540")
            self.pdf_var = tk.StringVar()
            self.images_var = tk.StringVar()
            self.template_var = tk.StringVar(value=str(default_template))
            self.output_var = tk.StringVar(value=str(Path.cwd() / "brochure_output.pptx"))
            self.model_var = tk.StringVar(value=default_model)
            self._build()

        def _build(self) -> None:
            frame = tk.Frame(self.root, padx=12, pady=12)
            frame.pack(fill="both", expand=True)
            self._row(frame, 0, "Listing PDF", self.pdf_var, lambda: self.pdf_var.set(filedialog.askopenfilename(filetypes=[("PDF files", "*.pdf")])))
            self._row(frame, 1, "Image Folder", self.images_var, lambda: self.images_var.set(filedialog.askdirectory()))
            self._row(frame, 2, "Template", self.template_var, lambda: self.template_var.set(filedialog.askopenfilename(filetypes=[("PowerPoint", "*.pptx")])))
            self._row(frame, 3, "Output", self.output_var, lambda: self.output_var.set(filedialog.asksaveasfilename(defaultextension=".pptx", filetypes=[("PowerPoint", "*.pptx")])))
            self._row(frame, 4, "Model", self.model_var, None)

            button_frame = tk.Frame(frame)
            button_frame.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(10, 10))
            tk.Button(button_frame, text="Generate Brochure", command=self.start_generation).pack(side="left")

            self.log_widget = ScrolledText(frame, height=20, wrap="word")
            self.log_widget.grid(row=6, column=0, columnspan=3, sticky="nsew")
            frame.columnconfigure(1, weight=1)
            frame.rowconfigure(6, weight=1)

        def _row(self, parent, row: int, label: str, variable, browse_command) -> None:
            tk.Label(parent, text=label, anchor="w").grid(row=row, column=0, sticky="w", pady=4)
            tk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=(8, 8))
            if browse_command:
                tk.Button(parent, text="Browse", command=browse_command).grid(row=row, column=2, sticky="ew")

        def append_log(self, message: str) -> None:
            self.log_widget.insert("end", message + "\n")
            self.log_widget.see("end")
            self.root.update_idletasks()

        def start_generation(self) -> None:
            config = AppConfig(
                pdf_path=Path(self.pdf_var.get()),
                image_dir=Path(self.images_var.get()),
                template_path=Path(self.template_var.get()),
                output_path=Path(self.output_var.get()),
                model=self.model_var.get().strip() or DEFAULT_MODEL,
            )
            try:
                validate_config(config)
            except Exception as exc:
                messagebox.showerror("Invalid input", str(exc))
                return
            thread = threading.Thread(target=self._run_generation, args=(config,), daemon=True)
            thread.start()

        def _run_generation(self, config: AppConfig) -> None:
            try:
                run_pipeline(config, logger=self.append_log)
                messagebox.showinfo("Success", f"Saved brochure to:\n{config.output_path}")
            except Exception as exc:
                self.append_log(traceback.format_exc())
                messagebox.showerror("Generation failed", str(exc))

    root = tk.Tk()
    BrochureApp(root)
    root.mainloop()
    return True


def run_cli(args: argparse.Namespace) -> int:
    if not args.pdf or not args.images:
        print("CLI mode requires --pdf and --images.", file=sys.stderr)
        return 2
    config = AppConfig(
        pdf_path=args.pdf,
        image_dir=args.images,
        template_path=args.template,
        output_path=args.output,
        model=args.model,
    )
    try:
        validate_config(config)
        run_pipeline(config)
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.cli:
        return run_cli(args)
    if maybe_launch_gui(args.template, args.model):
        return 0
    return run_cli(args)

