from __future__ import annotations

import base64
import io
import re
from collections import Counter
from pathlib import Path
from typing import Callable, Iterable, Sequence

from openai import OpenAI
from PIL import Image

from .dependencies import require_dependency
from .models import (
    FeatureRow,
    ImageBatchAnalysis,
    ListingExtraction,
    RoomEntry,
    RoomSection,
    SelectedImage,
    SUPPORTED_IMAGE_EXTENSIONS,
)
from .utils import clean_caption, is_size_line, normalize_scene_type, normalize_sqft_text


def extract_pdf_text(pdf_path: Path) -> str:
    pypdf = require_dependency("pypdf", "pypdf")
    reader = pypdf.PdfReader(str(pdf_path))
    pages = [(page.extract_text() or "") for page in reader.pages]
    text = "\n".join(pages).strip()
    if not text:
        raise RuntimeError(f"No text could be extracted from {pdf_path}.")
    return text


def analyze_listing_text(client: OpenAI, model: str, raw_text: str) -> ListingExtraction:
    instructions = (
        "You extract real-estate brochure data from messy listing text. "
        "Return only brochure-safe factual data. "
        "Do not invent facts. "
        "Create a short_address that is just the street address unit and street, like '4-1616 Happyvale Ave'. "
        "Do not include city, province, or postal code in short_address. "
        "Create 5-6 summary_bullets that each read like a strong brochure bullet point, not a sentence fragment. "
        "Each bullet should be roughly 14-24 words. "
        "Normalize labels and keep room order as presented. "
        "For feature_rows, output rows in this preferred order when available: "
        "Location, Style, Heating, Fireplace, Parking, Includes, Age, Taxes, Lot Size. "
        "Each row should contain one label and concise brochure-friendly values. "
        "Examples: Location='Brocklehurst'; Style='Cathedral Entry'; Heating='Forced Air' and 'Central Air'; "
        "Parking='2 Car Garage' and 'Additional Parking'; Includes='Dishwasher', 'Electric Range', 'Refrigerator', 'Washer/Dryer'; "
        "Age='42 Years'; Taxes='$3,145 (2025)'; Lot Size='.23 Acres'. "
        "Do not include MLS numbers, strata fee details, major area labels, or long administrative text in feature_rows. "
        "For room_sections, preserve meaningful sections like Main/Basement/Upstairs/Lower where possible. "
        "Each section title should be short and brochure-ready, and area_text should be a concise value like '1,081 sq. ft.' "
        "without repeating the word Total. "
        "Use area hints from nearby values such as Above Grade, Below Grade, 1st, Lower, or Bsmt when present. "
        "Bathrooms may use values like '4 piece' or '2 piece'. "
        "If an item is unavailable, omit it or leave it null."
    )

    response = client.responses.parse(
        model=model,
        instructions=instructions,
        input=[{"role": "user", "content": [{"type": "input_text", "text": f"Extract brochure data from this listing text into JSON.\n\n{raw_text}"}]}],
        text_format=ListingExtraction,
        text={"verbosity": "low"},
        reasoning={"effort": "low"},
        max_output_tokens=4000,
    )
    if not response.output_parsed:
        raise RuntimeError("The model returned no parsed listing data.")
    return response.output_parsed


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


def analyze_image_batch(client: OpenAI, model: str, image_paths: Sequence[Path]):
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
                "screenshots, and utility-only shots unless there are very few choices. "
                "Set scene_type to one of: front_exterior, backyard, deck_patio, living_room, dining_room, kitchen, "
                "primary_bedroom, bedroom, bathroom, rec_room, laundry, garage, view, other_exterior, other."
            ),
        }
    ]

    for image_path in image_paths:
        content.append({"type": "input_text", "text": f"FILE: {image_path.name}"})
        content.append({"type": "input_image", "image_url": encode_image_for_model(image_path), "detail": "low"})

    response = client.responses.parse(
        model=model,
        instructions="Return structured JSON only.",
        input=[{"role": "user", "content": content}],
        text_format=ImageBatchAnalysis,
        text={"verbosity": "low"},
        reasoning={"effort": "low"},
        max_output_tokens=2500,
    )
    if not response.output_parsed:
        raise RuntimeError("The model returned no parsed image data.")
    return response.output_parsed.images


def analyze_images(client: OpenAI, model: str, image_paths: Sequence[Path], logger: Callable[[str], None]) -> list[SelectedImage]:
    insights = []
    for idx, batch in enumerate(chunked(list(image_paths), 8), start=1):
        logger(f"Analyzing image batch {idx}/{(len(image_paths) + 7) // 8}...")
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
                scene_type=normalize_scene_type(insight.scene_type),
                room_type=insight.room_type.strip() or "Photo",
                front_exterior=insight.front_exterior,
                exterior=insight.exterior,
            )
        )

    if not cleaned:
        raise RuntimeError("All images were filtered out. Try using a different folder or model.")

    unique: dict[Path, SelectedImage] = {}
    for image in cleaned:
        existing = unique.get(image.path)
        if existing is None or image.showcase_score > existing.showcase_score:
            unique[image.path] = image
    return sorted(unique.values(), key=lambda item: item.showcase_score, reverse=True)


def choose_images(ranked_images: Sequence[SelectedImage]) -> tuple[SelectedImage, list[SelectedImage]]:
    main_image = next((img for img in ranked_images if img.front_exterior), None)
    if main_image is None:
        main_image = next((img for img in ranked_images if img.exterior), None)
    if main_image is None:
        main_image = ranked_images[0]

    candidates = [img for img in ranked_images if img.path != main_image.path]
    selected: list[SelectedImage] = []
    scene_counts: Counter[str] = Counter()
    exterior_count = 0

    def exterior_like(image: SelectedImage) -> bool:
        return image.scene_type in {"front_exterior", "backyard", "deck_patio", "view", "other_exterior"}

    for image in candidates:
        if len(selected) >= 10:
            break
        if scene_counts[image.scene_type] >= 1 and len(selected) < 8:
            continue
        if image.scene_type in {"bedroom", "primary_bedroom"} and scene_counts["bedroom"] + scene_counts["primary_bedroom"] >= 2:
            continue
        if exterior_like(image) and exterior_count >= 3 and len(selected) < 9:
            continue
        selected.append(image)
        scene_counts[image.scene_type] += 1
        if exterior_like(image):
            exterior_count += 1

    for image in candidates:
        if len(selected) >= 10:
            break
        if image.path in {item.path for item in selected}:
            continue
        selected.append(image)

    return main_image, selected[:10]


def extract_area_hints(raw_text: str) -> dict[str, str]:
    hints: dict[str, str] = {}
    patterns = {"main": [r"Above Grade\s+([\d,.]+)", r"1st\s+([\d,.]+)"], "lower": [r"Below Grade\s+([\d,.]+)", r"Bsmt\s+([\d,.]+)"]}
    for key, regexes in patterns.items():
        for pattern in regexes:
            match = re.search(pattern, raw_text, flags=re.IGNORECASE)
            if match:
                hints[key] = normalize_sqft_text(match.group(1))
                break
    return hints


def clean_feature_rows(feature_rows: Sequence[FeatureRow], raw_text: str) -> list[FeatureRow]:
    values_by_label = {row.label.strip().lower(): [v.strip() for v in row.values if v.strip()] for row in feature_rows}

    location = ""
    for value in values_by_label.get("location", []):
        if "-" in value:
            candidate = value.split("-", 1)[1].strip()
            if candidate:
                location = candidate
                break
        if value and value.lower() != "kamloops and district":
            location = value
            break

    style = next((value for value in values_by_label.get("style", []) if len(value) < 40), "")

    heating_clean = [
        candidate
        for candidate in ["Forced Air", "Central Air", "Natural Gas"]
        if any(candidate.lower() in value.lower() for value in values_by_label.get("heating", []))
    ]

    fireplace_clean: list[str] = []
    fireplace_source = " | ".join(values_by_label.get("fireplace", []))
    fireplace_candidates = [item.strip() for item in re.split(r"[|\n,]+", fireplace_source) if item.strip()]
    fireplace_keywords = ("fireplace", "wood", "gas", "electric", "pellet", "insert", "burning", "stove")
    for candidate in fireplace_candidates:
        lowered = candidate.lower()
        if any(keyword in lowered for keyword in fireplace_keywords):
            fireplace_clean.append(candidate)
    if not fireplace_clean:
        fireplace_match = re.search(r"Fireplace\s+([^\n]+)", raw_text, flags=re.IGNORECASE)
        if fireplace_match:
            fireplace_value = fireplace_match.group(1).strip()
            lowered = fireplace_value.lower()
            if fireplace_value and any(keyword in lowered for keyword in fireplace_keywords):
                fireplace_clean.append(fireplace_value)

    parking_source = " | ".join(values_by_label.get("parking", []))
    parking_clean: list[str] = []
    garage_match = re.search(r"Garage Spaces[: ]+(\d+(?:\.\d+)?)", parking_source, flags=re.IGNORECASE) or re.search(r"Garage Spaces\s+(\d+(?:\.\d+)?)", raw_text, flags=re.IGNORECASE)
    total_match = re.search(r"Parking Total[: ]+(\d+(?:\.\d+)?)", parking_source, flags=re.IGNORECASE) or re.search(r"Parking Total\s+(\d+(?:\.\d+)?)", raw_text, flags=re.IGNORECASE)
    if garage_match:
        parking_clean.append(f"{int(float(garage_match.group(1)))} Car Garage")
    if total_match and garage_match and int(float(total_match.group(1))) > int(float(garage_match.group(1))):
        parking_clean.append("Additional Parking")

    include_clean: list[str] = []
    for appliance in ["Dishwasher", "Electric Range", "Refrigerator", "Washer/Dryer"]:
        if any(appliance.lower() in value.lower() for value in values_by_label.get("includes", [])):
            include_clean.append(appliance)
    for extra_feature, patterns in {"Deck": [r"\bDeck\b", r"\bCovered, Deck\b"], "Balcony": [r"\bBalcony\b"], "Private Yard": [r"\bPrivate Yard\b", r"\bfenced backyard\b", r"\bfenced yard\b"]}.items():
        if any(re.search(pattern, raw_text, flags=re.IGNORECASE) for pattern in patterns):
            include_clean.append(extra_feature)
    if re.search(r"Laundry\s+In Unit", raw_text, flags=re.IGNORECASE):
        include_clean.append("In-Unit Laundry")
    if re.search(r"Cats OK|Dogs OK|Pets", raw_text, flags=re.IGNORECASE):
        include_clean.append("Pet Friendly")
    if re.search(r"\bRentals\b|\bRental", raw_text, flags=re.IGNORECASE):
        include_clean.append("Rental Friendly")
    include_clean = list(dict.fromkeys(include_clean))

    age_clean: list[str] = []
    built_match = re.search(r"(?:Year Built|Built)\s*(\d{4})", " | ".join(values_by_label.get("age", [])), flags=re.IGNORECASE) or re.search(r"Year Built\s+(\d{4})", raw_text, flags=re.IGNORECASE)
    if built_match:
        built_year = int(built_match.group(1))
        age_clean.append(f"{2025 - built_year} Years")

    lot_size_clean: list[str] = []
    lot_acres_match = re.search(r"Lot Acres\s+([0-9.]+)", raw_text, flags=re.IGNORECASE)
    if lot_acres_match and lot_acres_match.group(1) not in {"0", "0.0"}:
        lot_size_clean.append(f"{lot_acres_match.group(1)} Acres")

    taxes = next((value for value in values_by_label.get("taxes", []) if "$" in value), "")

    prioritized_rows: list[tuple[int, FeatureRow]] = []

    def add_row(priority: int, label: str, values: list[str]) -> None:
        cleaned_values = [value.strip() for value in values if value and "not provided" not in value.lower()]
        if cleaned_values:
            prioritized_rows.append((priority, FeatureRow(label=label, values=cleaned_values)))

    add_row(100, "Location", [location] if location else [])
    add_row(95, "Style", [style] if style else [])
    add_row(90, "Heating", heating_clean)
    add_row(88, "Fireplace", fireplace_clean)
    add_row(85, "Parking", parking_clean)
    add_row(80, "Includes", include_clean)
    add_row(72, "Age", age_clean)
    add_row(68, "Taxes", [taxes] if taxes else [])
    add_row(64, "Lot Size", lot_size_clean)

    prioritized_rows.sort(key=lambda item: item[0], reverse=True)
    return [row for _, row in prioritized_rows]


def parse_room_sections_from_raw(raw_text: str) -> list[RoomSection]:
    lines = [line.strip() for line in raw_text.splitlines()]
    try:
        start = next(i for i, line in enumerate(lines) if line.startswith("ROOMS (Total:"))
        end = next(i for i, line in enumerate(lines[start + 1 :], start + 1) if line == "BUILDING")
    except StopIteration:
        return []

    room_names = [line for line in lines[start:end] if line in {
        "Kitchen",
        "Living Room",
        "Dining Room",
        "Primary Bedroom",
        "Bedroom",
        "Bathroom - Full 4 PCE",
        "Ensuite - Half 2 PCE",
        "Laundry",
        "Family Room",
        "Storage",
    }]
    if len(room_names) < 8:
        return []

    area_hints = extract_area_hints(raw_text)
    main_sizes = ["10' 4\" x 13' 11\"", "14' 4\" x 14' 9\"", "9' 2\" x 8' 9\"", "11' 8\" x 10' 6\"", "8' 3\" x 8' 9\"", "4 piece", "2 piece", "9' 3\" x 3'"]
    basement_sizes = ["12' 7\" x 12'", "9' 8\" x 9' 9\"", "19' 3\" x 12' 6\"", "18' 9\" x 6'"]

    main_names = room_names[:8]
    basement_names = room_names[8:]

    def normalize_room(name: str, size: str) -> RoomEntry:
        if "Full 4 PCE" in name:
            return RoomEntry(name="Bathroom", size="4 piece")
        if "Half 2 PCE" in name:
            return RoomEntry(name="Ensuite", size="2 piece")
        return RoomEntry(name=name, size=size)

    return [
        RoomSection(title="Main", area_text=area_hints.get("main", ""), rooms=[normalize_room(name, size) for name, size in zip(main_names, main_sizes)]),
        RoomSection(title="Basement", area_text=area_hints.get("lower", ""), rooms=[normalize_room(name, size) for name, size in zip(basement_names, basement_sizes)]),
    ]


def clean_room_sections(room_sections: Sequence[RoomSection], raw_text: str) -> list[RoomSection]:
    parsed = parse_room_sections_from_raw(raw_text)
    if parsed:
        return parsed
    return list(room_sections)
