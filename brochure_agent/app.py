from __future__ import annotations

import argparse
import sys
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


def maybe_launch_gui(default_template: Path, default_model: str) -> tuple[bool, str]:
    try:
        from .gui import launch_gui
    except Exception as exc:
        return False, str(exc)
    try:
        return launch_gui(default_template, default_model), ""
    except Exception as exc:
        return False, str(exc)


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
    launched_gui, gui_error = maybe_launch_gui(args.template, args.model)
    if launched_gui:
        return 0
    if not args.pdf and not args.images:
        print(f"GUI mode is unavailable: {gui_error}", file=sys.stderr)
        print("Install Tkinter/Tk, or run with --cli --pdf ... --images ...", file=sys.stderr)
        return 1
    return run_cli(args)
