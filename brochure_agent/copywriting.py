from __future__ import annotations

from collections.abc import Sequence

from openai import OpenAI

from .models import BrochureCopyResponse, CaptionItem, ListingExtraction, SelectedImage
from .utils import clean_bullet_text, clean_caption


def format_feature_rows_for_prompt(feature_rows) -> str:
    parts: list[str] = []
    for row in feature_rows:
        values = ", ".join(value.strip() for value in row.values if value.strip())
        if values:
            parts.append(f"{row.label}: {values}")
    return " | ".join(parts)


def generate_brochure_copy(
    client: OpenAI,
    model: str,
    listing: ListingExtraction,
    main_image: SelectedImage,
    gallery_images: Sequence[SelectedImage],
) -> BrochureCopyResponse:
    gallery_summary = "\n".join(
        f"- {image.path.name} | scene={image.scene_type} | room={image.room_type} | draft={image.caption}"
        for image in gallery_images
    )
    response = client.responses.parse(
        model=model,
        instructions=(
            "Write brochure copy for a real-estate flyer. "
            "Return one title, 5-6 summary bullets, and one caption object for each gallery image. "
            "The title must read like a polished headline, not a list of features. "
            "It should evoke the home and lifestyle, be specific, and stay in title case. "
            "Avoid comma-spliced feature lists. "
            "Each summary bullet should be a complete, brochure-ready point of roughly 14-24 words. "
            "Each caption object must include the exact file_name provided for that image. "
            "Each caption should be balanced and concise, ideally 70-110 characters, and should fit a small caption box. "
            "Do not repeat nearly identical captions. "
            "Do not mention file names in the caption text."
        ),
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            f"Short address: {listing.short_address}\n"
                            f"Full address: {listing.address}\n"
                            f"Price: {listing.price}\n"
                            f"Beds/Baths: {listing.bedrooms} / {listing.bathrooms}\n"
                            f"Features: {format_feature_rows_for_prompt(listing.feature_rows)}\n"
                            f"Listing bullets: {' | '.join(listing.summary_bullets)}\n"
                            f"Main image: scene={main_image.scene_type} draft={main_image.caption}\n"
                            f"Other images in required caption order:\n{gallery_summary}"
                        ),
                    }
                ],
            }
        ],
        text_format=BrochureCopyResponse,
        text={"verbosity": "low"},
        reasoning={"effort": "low"},
        max_output_tokens=600,
    )
    parsed = response.output_parsed
    if not parsed or not parsed.title:
        raise RuntimeError("The model returned no brochure copy.")
    parsed.title = " ".join(parsed.title.split())
    parsed.summary_bullets = [clean_bullet_text(item) for item in parsed.summary_bullets if item.strip()]
    parsed.captions = [CaptionItem(file_name=item.file_name, caption=clean_caption(item.caption)) for item in parsed.captions if item.file_name.strip() and item.caption.strip()]
    return parsed

