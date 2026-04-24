from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field


SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
DEFAULT_MODEL = "gpt-5.4"


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
    short_address: str
    price: str
    bedrooms: int | None = None
    bathrooms: int | None = None
    total_sqft: str | None = None
    summary_bullets: list[str] = Field(default_factory=list)
    feature_rows: list[FeatureRow] = Field(default_factory=list)
    room_sections: list[RoomSection] = Field(default_factory=list)


class ImageInsight(BaseModel):
    file_name: str
    scene_type: str
    room_type: str
    caption: str
    showcase_score: int = Field(ge=1, le=10)
    front_exterior: bool = False
    exterior: bool = False
    avoid: bool = False
    avoid_reason: str = ""


class ImageBatchAnalysis(BaseModel):
    images: list[ImageInsight]


class CaptionItem(BaseModel):
    file_name: str
    caption: str


class BrochureCopyResponse(BaseModel):
    title: str
    summary_bullets: list[str] = Field(default_factory=list)
    captions: list[CaptionItem] = Field(default_factory=list)


@dataclass
class SelectedImage:
    path: Path
    caption: str
    showcase_score: int
    scene_type: str
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

