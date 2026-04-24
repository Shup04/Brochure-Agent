from __future__ import annotations

from pathlib import Path
from typing import Callable

from .copywriting import generate_brochure_copy
from .dependencies import build_client
from .models import AppConfig
from .parsing import (
    analyze_images,
    analyze_listing_text,
    choose_images,
    clean_feature_rows,
    clean_room_sections,
    extract_pdf_text,
    iter_image_paths,
)
from .rendering import build_text_mapping, render_presentation
from .utils import log_print


def validate_config(config: AppConfig) -> None:
    if not config.pdf_path.exists():
        raise RuntimeError(f"PDF not found: {config.pdf_path}")
    if not config.image_dir.exists() or not config.image_dir.is_dir():
        raise RuntimeError(f"Image directory not found: {config.image_dir}")
    if not config.template_path.exists():
        raise RuntimeError(f"Template not found: {config.template_path}")
    config.output_path.parent.mkdir(parents=True, exist_ok=True)


def run_pipeline(config: AppConfig, logger: Callable[[str], None] = log_print) -> Path:
    logger("Reading listing PDF...")
    raw_text = extract_pdf_text(config.pdf_path)

    client = build_client()

    logger("Extracting brochure fields from listing text...")
    listing = analyze_listing_text(client, config.model, raw_text)
    listing.feature_rows = clean_feature_rows(listing.feature_rows, raw_text)
    listing.room_sections = clean_room_sections(listing.room_sections, raw_text)

    logger("Scanning image folder...")
    image_paths = iter_image_paths(config.image_dir)
    logger(f"Found {len(image_paths)} image(s).")

    ranked_images = analyze_images(client, config.model, image_paths, logger)
    main_image, gallery_images = choose_images(ranked_images)

    logger("Generating brochure copy...")
    brochure_copy = generate_brochure_copy(client, config.model, listing, main_image, gallery_images)
    caption_map = {item.file_name: item.caption for item in brochure_copy.captions}
    for image in gallery_images:
        if image.path.name in caption_map:
            image.caption = caption_map[image.path.name]

    text_mapping = build_text_mapping(listing, brochure_copy, gallery_images)
    text_mapping["{{SUMMARY}}"] = "\n".join(brochure_copy.summary_bullets or listing.summary_bullets)

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

