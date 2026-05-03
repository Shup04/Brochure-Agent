from __future__ import annotations

import re


def log_print(message: str) -> None:
    print(message)


def clean_bullet_text(text: str) -> str:
    return " ".join(text.split()).lstrip("-• ").strip()


def clean_caption(text: str) -> str:
    value = " ".join(text.split())
    if not value:
        return "Beautiful space with strong natural light and inviting everyday comfort."
    value = value.strip()

    if len(value) <= 110 and value[-1] in ".!?":
        return value
    if len(value) <= 100:
        return f"{value.rstrip('.!? ')}."

    complete_sentence = re.match(r"^(.{45,110}?[.!?])(?:\s|$)", value)
    if complete_sentence:
        return complete_sentence.group(1).strip()

    trimmed = value[:100].rsplit(" ", 1)[0].rstrip(",;:- ")
    return f"{trimmed}."


def normalize_scene_type(scene_type: str) -> str:
    value = scene_type.strip().lower().replace(" ", "_")
    allowed = {
        "front_exterior",
        "backyard",
        "deck_patio",
        "living_room",
        "dining_room",
        "kitchen",
        "primary_bedroom",
        "bedroom",
        "bathroom",
        "rec_room",
        "laundry",
        "garage",
        "view",
        "other_exterior",
        "other",
    }
    return value if value in allowed else "other"


def format_beds_and_baths(bedrooms: int | None, bathrooms: int | None) -> str:
    bed_text = f"{bedrooms} Bedroom" if bedrooms == 1 else f"{bedrooms} Bedrooms" if bedrooms else "Bedrooms"
    bath_text = f"{bathrooms} Bath" if bathrooms == 1 else f"{bathrooms} Baths" if bathrooms else "Baths"
    return f"{bed_text} & {bath_text}"


def normalize_sqft_text(value: str) -> str:
    compact = " ".join(value.split())
    compact = re.sub(r"\(Total\)", "", compact, flags=re.IGNORECASE).strip()
    match = re.search(r"[\d,.]+", compact)
    if not match:
        return compact
    raw_number = match.group(0).replace(",", "")
    try:
        number = float(raw_number)
    except ValueError:
        return f"{match.group(0)} sq. ft."
    if number.is_integer() or len(raw_number.split(".")[-1]) > 1:
        formatted = f"{round(number):,}"
    else:
        formatted = f"{number:,.1f}"
    return f"{formatted} sq. ft."


def format_total(total_sqft: str | None) -> str:
    if not total_sqft:
        return "Total"
    return f"Total ({normalize_sqft_text(total_sqft)})"


def is_size_line(text: str) -> bool:
    value = " ".join(text.split()).strip()
    if not value:
        return False
    if value == "x":
        return True
    return bool(re.search(r"\d+'\s*\d*\"?\s*x\s*\d+'\s*\d*\"?", value))
