from __future__ import annotations

import argparse
import base64
import io
import os
import sys
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

from openai import OpenAI
from pydantic import BaseModel, Field
from PIL import Image


SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
DEFAULT_MODEL = "gpt-5.4-nano"


class FeatureRow(BaseModel):
    label: str
    values: list[str] = Field(default_factory=list)


class RoomEntry(BaseModel):
    name: str
    size: str


class RoomSection(BaseModel):
    title: str
    area_text: str
    rooms: list[RoomEntry] = Field(default_factory=list)


class ListingExtraction(BaseModel):
    address: str
    price: str
    bedrooms: int | None = None
    bathrooms: int | None = None
    total_sqft: str | None = None
    summary: str = ""
    feature_rows: list[FeatureRow] = Field(default_factory=list)
    room_sections: list[RoomSection] = Field(default_factory=list)


class ImageInsight(BaseModel):
    file_name: str
    room_type: str
    caption: str
    showcase_score: int = Field(ge=1, le=10)
    front_exterior: bool = False
    exterior: bool = False
    avoid: bool = False
    avoid_reason: str = ""


class ImageBatchAnalysis(BaseModel):
    images: list[ImageInsight]


class TitleResponse(BaseModel):
    title: str


@dataclass
class SelectedImage:
    path: Path
    caption: str
    showcase_score: int
    room_type: str
    front_exterior: bool
    exterior: bool


@dataclass
class AppConfig:
    pdf_path: Path
    image_dir: Path
    template_path: Path
    output_path: Path
    model: str = DEFAULT_MODEL


def log_print(message: str) -> None:
    print(message)


def require_dependency(module_name: str, package_name: str):
    try:
        return __import__(module_name, fromlist=["*"])
    except ImportError as exc:
        raise RuntimeError(
            f"Missing dependency '{package_name}'. Install the packages from requirements.txt first."
        ) from exc


def build_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")

    base_url = os.getenv("OPENAI_BASE_URL")
    if base_url:
        return OpenAI(api_key=api_key, base_url=base_url)
    return OpenAI(api_key=api_key)


def extract_pdf_text(pdf_path: Path) -> str:
    pypdf = require_dependency("pypdf", "pypdf")
    reader = pypdf.PdfReader(str(pdf_path))
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    text = "\n".join(pages).strip()
    if not text:
        raise RuntimeError(f"No text could be extracted from {pdf_path}.")
    return text


def analyze_listing_text(client: OpenAI, model: str, raw_text: str) -> ListingExtraction:
    instructions = (
        "You extract real-estate brochure data from messy listing text. "
        "Return only brochure-safe factual data. "
        "Do not invent facts. "
        "Normalize labels and keep room order as presented. "
        "For feature_rows, output rows in this preferred order when available: "
        "Location, Style, Heating, Fireplace, Parking, Includes, Age, Taxes, Lot Size. "
        "Each row should contain a label and one or more display lines in values. "
        "For room_sections, preserve the section headings and room ordering. "
        "If an item is unavailable, omit it or leave it null."
    )

    response = client.responses.parse(
        model=model,
        instructions=instructions,
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Extract brochure data from this listing text into JSON.\n\n"
                            f"{raw_text}"
                        ),
                    }
                ],
            }
        ],
        text_format=ListingExtraction,
        text={"verbosity": "low"},
        reasoning={"effort": "low"},
        max_output_tokens=4000,
    )
    parsed = response.output_parsed
    if not parsed:
        raise RuntimeError("The model returned no parsed listing data.")
    return parsed


def iter_image_paths(image_dir: Path) -> list[Path]:
    images = [p for p in sorted(image_dir.iterdir()) if p.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS]
    if not images:
        raise RuntimeError(f"No supported images were found in {image_dir}.")
    return images


def encode_image_for_model(image_path: Path, max_size: int = 1024) -> str:
    with Image.open(image_path) as img:
        image = img.convert("RGB")
        image.thumbnail((max_size, max_size))
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=72, optimize=True)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def chunked(items: Sequence[Path], size: int) -> Iterable[Sequence[Path]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def analyze_image_batch(client: OpenAI, model: str, image_paths: Sequence[Path]) -> list[ImageInsight]:
    content: list[dict] = [
        {
            "type": "input_text",
            "text": (
                "Review these real-estate listing photos for brochure selection. "
                "For each image, determine if it should be avoided, whether it is the front exterior, "
                "whether it is another exterior, assign a showcase_score from 1-10, classify the room_type, "
                "and write a short brochure caption that highlights the best feature of the photo. "
                "Prefer bright, wide, well-composed photos that help sell the house. "
                "Avoid blurry images, duplicate angles, closeups with little context, floorplans, logos, "
                "screenshots, and utility-only shots unless there are very few choices."
            ),
        }
    ]

    for image_path in image_paths:
        content.append({"type": "input_text", "text": f"FILE: {image_path.name}"})
        content.append(
            {
                "type": "input_image",
                "image_url": encode_image_for_model(image_path),
                "detail": "low",
            }
        )

    response = client.responses.parse(
        model=model,
        instructions="Return structured JSON only.",
        input=[{"role": "user", "content": content}],
        text_format=ImageBatchAnalysis,
        text={"verbosity": "low"},
        reasoning={"effort": "low"},
        max_output_tokens=2500,
    )
    parsed = response.output_parsed
    if not parsed:
        raise RuntimeError("The model returned no parsed image data.")
    return parsed.images


def analyze_images(
    client: OpenAI, model: str, image_paths: Sequence[Path], logger: Callable[[str], None]
) -> list[SelectedImage]:
    insights: list[ImageInsight] = []
    batches = list(chunked(list(image_paths), 8))
    for idx, batch in enumerate(batches, start=1):
        logger(f"Analyzing image batch {idx}/{len(batches)}...")
        insights.extend(analyze_image_batch(client, model, batch))

    path_by_name = {path.name: path for path in image_paths}
    cleaned: list[SelectedImage] = []
    for insight in insights:
        image_path = path_by_name.get(insight.file_name)
        if not image_path or insight.avoid:
            continue
        cleaned.append(
            SelectedImage(
                path=image_path,
                caption=clean_caption(insight.caption),
                showcase_score=insight.showcase_score,
                room_type=insight.room_type.strip() or "Photo",
                front_exterior=insight.front_exterior,
                exterior=insight.exterior,
            )
        )

    if not cleaned:
        raise RuntimeError("All images were filtered out. Try using a different folder or model.")

    # Keep the highest-scoring image per filename and sort strongest first.
    unique: dict[Path, SelectedImage] = {}
    for image in cleaned:
        existing = unique.get(image.path)
        if existing is None or image.showcase_score > existing.showcase_score:
            unique[image.path] = image
    ranked = sorted(unique.values(), key=lambda item: item.showcase_score, reverse=True)
    return ranked


def clean_caption(text: str) -> str:
    value = " ".join(text.split())
    return value[:140].rstrip(". ") + "." if value else "Beautiful view of the home."


def choose_images(ranked_images: Sequence[SelectedImage]) -> tuple[SelectedImage, list[SelectedImage]]:
    main_image = next((img for img in ranked_images if img.front_exterior), None)
    if main_image is None:
        main_image = next((img for img in ranked_images if img.exterior), None)
    if main_image is None:
        main_image = ranked_images[0]

    gallery = [img for img in ranked_images if img.path != main_image.path]
    if len(gallery) < 10:
        gallery = list(gallery)
        if main_image not in gallery:
            gallery.insert(0, main_image)
    return main_image, gallery[:10]


def generate_title(
    client: OpenAI,
    model: str,
    listing: ListingExtraction,
    main_image: SelectedImage,
    gallery_images: Sequence[SelectedImage],
) -> str:
    gallery_summary = "\n".join(f"- {image.room_type}: {image.caption}" for image in gallery_images[:5])
    response = client.responses.parse(
        model=model,
        instructions=(
            "Write one concise real-estate brochure headline in title case. "
            "Keep it specific, polished, and not cheesy. "
            "Do not include the price. "
            "Aim for 8-16 words."
        ),
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            f"Address: {listing.address}\n"
                            f"Summary: {listing.summary}\n"
                            f"Main image: {main_image.caption}\n"
                            f"Other images:\n{gallery_summary}"
                        ),
                    }
                ],
            }
        ],
        text_format=TitleResponse,
        text={"verbosity": "low"},
        reasoning={"effort": "low"},
        max_output_tokens=100,
    )
    parsed = response.output_parsed
    if not parsed or not parsed.title:
        raise RuntimeError("The model returned no title.")
    return " ".join(parsed.title.split())


def build_text_mapping(
    listing: ListingExtraction, title: str, gallery_images: Sequence[SelectedImage]
) -> dict[str, str]:
    mapping = {
        "{{ADDRESS}}": listing.address,
        "{{PRICE}}": listing.price,
        "{{BEDS_AND_BATHS}}": format_beds_and_baths(listing.bedrooms, listing.bathrooms),
        "{{TITLE}}": title,
        "{{SUMMARY}}": listing.summary,
        "{{TOTAL}}": format_total(listing.total_sqft),
    }
    for index in range(1, 11):
        caption = gallery_images[index - 1].caption if index <= len(gallery_images) else ""
        mapping[f"{{{{caption_{index}}}}}"] = caption
    return mapping


def format_beds_and_baths(bedrooms: int | None, bathrooms: int | None) -> str:
    bed_text = f"{bedrooms} Bedroom" if bedrooms == 1 else f"{bedrooms} Bedrooms" if bedrooms else "Bedrooms"
    bath_text = f"{bathrooms} Bath" if bathrooms == 1 else f"{bathrooms} Baths" if bathrooms else "Baths"
    return f"{bed_text} & {bath_text}"


def format_total(total_sqft: str | None) -> str:
    if not total_sqft:
        return "Total"
    return f"Total ({total_sqft} sq. ft.)"


def clone_run_style(source_run, dest_run) -> None:
    if source_run is None:
        return
    font = source_run.font
    dest_font = dest_run.font
    dest_font.bold = font.bold
    dest_font.italic = font.italic
    dest_font.name = font.name
    dest_font.size = font.size
    if font.color is not None and getattr(font.color, "rgb", None) is not None:
        dest_font.color.rgb = font.color.rgb


def replace_text_in_shape(shape, replacements: dict[str, str]) -> None:
    if not getattr(shape, "has_text_frame", False):
        return

    for paragraph in shape.text_frame.paragraphs:
        original_text = "".join(run.text for run in paragraph.runs)
        if not original_text:
            continue
        updated_text = original_text
        for placeholder, replacement in replacements.items():
            updated_text = updated_text.replace(placeholder, replacement)
        if updated_text == original_text:
            continue

        source_run = paragraph.runs[0] if paragraph.runs else None
        alignment = paragraph.alignment
        level = paragraph.level
        paragraph.text = updated_text
        paragraph.alignment = alignment
        paragraph.level = level
        if paragraph.runs:
            clone_run_style(source_run, paragraph.runs[0])


def populate_feature_block(shape, feature_rows: Sequence[FeatureRow]) -> None:
    if not getattr(shape, "has_text_frame", False):
        return
    text_frame = shape.text_frame
    text_frame.clear()

    first = True
    for row in feature_rows:
        values = [value.strip() for value in row.values if value.strip()]
        if not values:
            continue

        paragraph = text_frame.paragraphs[0] if first else text_frame.add_paragraph()
        first = False
        paragraph.text = f"{row.label}:\t{values[0]}"
        paragraph.space_after = 0
        paragraph.space_before = 0
        paragraph.line_spacing = 1.0
        for run in paragraph.runs:
            run.font.bold = True
            run.font.name = "Nimbus Roman"
            run.font.size = paragraph.runs[0].font.size

        for extra_value in values[1:]:
            extra = text_frame.add_paragraph()
            extra.text = f"\t{extra_value}"
            extra.space_after = 0
            extra.space_before = 0
            extra.line_spacing = 1.0
            for run in extra.runs:
                run.font.bold = True
                run.font.name = "Nimbus Roman"


def populate_room_block(shape, room_sections: Sequence[RoomSection]) -> None:
    if not getattr(shape, "has_text_frame", False):
        return
    from pptx.enum.text import PP_ALIGN

    text_frame = shape.text_frame
    text_frame.clear()
    first = True

    for section in room_sections:
        heading = text_frame.paragraphs[0] if first else text_frame.add_paragraph()
        first = False
        heading.alignment = PP_ALIGN.CENTER
        heading.text = f"{section.title} ({section.area_text})"
        heading.space_after = 0
        heading.space_before = 0
        for run in heading.runs:
            run.font.bold = True
            run.font.italic = True
            run.font.name = "Nimbus Roman"

        spacer = text_frame.add_paragraph()
        spacer.text = ""

        for room in section.rooms:
            row = text_frame.add_paragraph()
            row.text = f"{room.name}\t{room.size}"
            row.space_after = 0
            row.space_before = 0
            for run in row.runs:
                run.font.bold = True
                run.font.name = "Nimbus Roman"

        spacer = text_frame.add_paragraph()
        spacer.text = ""


def remove_shape(shape) -> None:
    shape._element.getparent().remove(shape._element)


def fill_picture_placeholder(slide, placeholder_shape, image_path: Path) -> None:
    from pptx.util import Emu

    left, top, width, height = (
        placeholder_shape.left,
        placeholder_shape.top,
        placeholder_shape.width,
        placeholder_shape.height,
    )

    with Image.open(image_path) as img:
        image_width, image_height = img.size

    image_ratio = image_width / image_height
    box_ratio = width / height

    picture = slide.shapes.add_picture(str(image_path), left, top, width=width, height=height)
    if image_ratio > box_ratio:
        scaled_width = height * image_ratio
        crop = (scaled_width - width) / scaled_width / 2
        picture.crop_left = crop
        picture.crop_right = crop
    else:
        scaled_height = width / image_ratio
        crop = (scaled_height - height) / scaled_height / 2
        picture.crop_top = crop
        picture.crop_bottom = crop

    remove_shape(placeholder_shape)


def render_presentation(
    template_path: Path,
    output_path: Path,
    text_mapping: dict[str, str],
    feature_rows: Sequence[FeatureRow],
    room_sections: Sequence[RoomSection],
    main_image: SelectedImage,
    gallery_images: Sequence[SelectedImage],
) -> None:
    pptx = require_dependency("pptx", "python-pptx")
    presentation = pptx.Presentation(str(template_path))

    image_map = {"{{IMG_MAIN}}": main_image.path}
    for index, image in enumerate(gallery_images, start=1):
        image_map[f"{{{{IMG_{index}}}}}"] = image.path

    for slide in presentation.slides:
        for shape in list(slide.shapes):
            shape_name = getattr(shape, "name", "").strip()
            if shape_name in image_map:
                fill_picture_placeholder(slide, shape, image_map[shape_name])
                continue

            if getattr(shape, "has_text_frame", False):
                combined_text = "\n".join("".join(run.text for run in paragraph.runs) for paragraph in shape.text_frame.paragraphs)
                if "{{FEATURE_BLOCK}}" in combined_text:
                    populate_feature_block(shape, feature_rows)
                    continue
                if "{{ROOM_BLOCK}}" in combined_text:
                    populate_room_block(shape, room_sections)
                    continue
                replace_text_in_shape(shape, text_mapping)

    presentation.save(str(output_path))


def run_pipeline(config: AppConfig, logger: Callable[[str], None] = log_print) -> Path:
    logger("Reading listing PDF...")
    raw_text = extract_pdf_text(config.pdf_path)

    client = build_client()

    logger("Extracting brochure fields from listing text...")
    listing = analyze_listing_text(client, config.model, raw_text)

    logger("Scanning image folder...")
    image_paths = iter_image_paths(config.image_dir)

    logger(f"Found {len(image_paths)} image(s).")
    ranked_images = analyze_images(client, config.model, image_paths, logger)
    main_image, gallery_images = choose_images(ranked_images)

    logger("Generating brochure title...")
    title = generate_title(client, config.model, listing, main_image, gallery_images)

    text_mapping = build_text_mapping(listing, title, gallery_images)

    logger("Rendering PowerPoint brochure...")
    render_presentation(
        template_path=config.template_path,
        output_path=config.output_path,
        text_mapping=text_mapping,
        feature_rows=listing.feature_rows,
        room_sections=listing.room_sections,
        main_image=main_image,
        gallery_images=gallery_images,
    )

    logger(f"Saved brochure to {config.output_path}")
    return config.output_path


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a real-estate brochure PPTX from a listing PDF and image folder.")
    parser.add_argument("--pdf", type=Path, help="Path to the listing PDF.")
    parser.add_argument("--images", type=Path, help="Path to the image directory.")
    parser.add_argument("--template", type=Path, default=Path("Brochure_Template.pptx"), help="Path to the PPTX template.")
    parser.add_argument("--output", type=Path, default=Path("brochure_output.pptx"), help="Output PPTX path.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="OpenAI model name.")
    parser.add_argument("--cli", action="store_true", help="Force CLI mode even if tkinter is available.")
    return parser.parse_args(argv)


def validate_config(config: AppConfig) -> None:
    if not config.pdf_path.exists():
        raise RuntimeError(f"PDF not found: {config.pdf_path}")
    if not config.image_dir.exists() or not config.image_dir.is_dir():
        raise RuntimeError(f"Image directory not found: {config.image_dir}")
    if not config.template_path.exists():
        raise RuntimeError(f"Template not found: {config.template_path}")
    config.output_path.parent.mkdir(parents=True, exist_ok=True)


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


if __name__ == "__main__":
    raise SystemExit(main())
