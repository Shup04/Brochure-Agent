"""Synthetic offline acceptance using the production CLI and pipeline."""
from __future__ import annotations

import argparse
import hashlib
import json
import socket
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches, Pt
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from brochure_agent.app import main
from brochure_agent.models import (
    BrochureCopyResponse, CaptionItem, FeatureRow, ImageBatchAnalysis,
    ImageInsight, ListingExtraction, RoomEntry, RoomSection,
)

ROOT = Path(__file__).resolve().parents[1]
SCENES = ['front_exterior', 'living_room', 'kitchen', 'dining_room',
          'primary_bedroom', 'bedroom', 'backyard', 'deck_patio',
          'rec_room', 'bathroom', 'garage', 'other']
LISTING_TEXT = '''FICTIONAL ACCEPTANCE FIXTURE - NOT A REAL LISTING
123 Example Avenue
Example Town
Price $450,000
Bedrooms 3 Bathrooms 2
Total 1,200 sq. ft.
Year Built 2000
Style Ranch
Heating Forced Air
Garage Spaces 1
Parking Total 2
Lot Acres 0.2
Main 1,200 sq. ft.
Living Room 12 x 14
Kitchen 10 x 12
Bedroom 10 x 10
'''


def write_pdf(path: Path, text: str) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                             NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
    lines = [line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)') for line in text.splitlines()]
    stream = DecodedStreamObject()
    stream.set_data(('BT /F1 12 Tf 40 750 Td 16 TL ' + ' '.join(f'({line}) Tj T*' for line in lines) + ' ET').encode('ascii'))
    page[NameObject('/Contents')] = writer._add_object(stream)
    writer.write(path)


def create_fixtures(directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'listing.txt').write_text(LISTING_TEXT)
    pdf = directory / 'listing.pdf'
    write_pdf(pdf, LISTING_TEXT)
    images = directory / 'photos'
    images.mkdir(exist_ok=True)
    for index, scene in enumerate(SCENES):
        # Deliberately vary aspect ratios so center cropping is exercised.
        size = (900, 600) if index % 2 == 0 else (600, 900)
        image = Image.new('RGB', size, (40 + index * 15, 90 + index * 9, 160 - index * 8))
        draw = ImageDraw.Draw(image)
        w, h = size
        draw.rectangle((w * .15, h * .2, w * .85, h * .8), outline='white', width=8)
        draw.line((w / 2, h * .2, w / 2, h * .8), fill='white', width=5)
        draw.text((w / 2, h / 2 - 25), f'FIXTURE {index:02}', fill='white', anchor='mm', font_size=40)
        draw.text((w / 2, h / 2 + 25), scene.replace('_', ' ').upper(), fill='white', anchor='mm', font_size=24)
        image.save(images / f'{index:02}_{scene}.png')
    return pdf, images


def create_demo_template(directory: Path) -> Path:
    """Keep the supplied placeholder geometry, remove baked-in listing imagery.

    The original background is a raster of an old brochure, including agency
    branding, an MLS number and a source-of-measurements claim. It is not fixture
    data and must not appear in a shareable fictional acceptance preview.
    """
    p = Presentation(ROOT / 'Brochure_Template.pptx')
    for slide in p.slides:
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = RGBColor(255, 255, 255)
        for shape in list(slide.shapes):
            if shape.shape_type == 13:
                shape._element.getparent().remove(shape._element)
        for left in (.18, 3.88, 7.58):
            border = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(left), Inches(.17), Inches(3.27), Inches(8.1))
            border.fill.background()
            border.line.color.rgb = RGBColor(80, 80, 80)
        label = slide.shapes.add_textbox(Inches(7.8), Inches(.92), Inches(2.8), Inches(.4))
        label.text = 'FICTIONAL ACCEPTANCE DEMO'
        label.text_frame.paragraphs[0].runs[0].font.size = Pt(10)
    destination = directory / 'synthetic-template.pptx'
    p.save(destination)
    return destination


def fixture_caption(name: str) -> str:
    return f'Fixture {name[:2]}: the {name[3:-4].replace("_", " ")} panel verifies photo ordering and center cropping.'


class FakeClient:
    """Return typed fixtures at the model boundary; no HTTP client is created."""
    def __init__(self):
        self.responses = self
        self.calls = []
        self.selected_files = []

    def parse(self, **kwargs):
        schema = kwargs['text_format']
        content = kwargs['input'][0]['content']
        self.calls.append(schema.__name__)
        if schema is ListingExtraction:
            assert 'FICTIONAL ACCEPTANCE FIXTURE' in content[0]['text']
            result = ListingExtraction(
                address='123 Example Avenue, Example Town', short_address='123 Example Avenue',
                price='$450,000', bedrooms=3, bathrooms=2, total_sqft='1,200',
                summary_bullets=['This fictional three bedroom home has two bathrooms and a single garage.'],
                feature_rows=[FeatureRow(label='Style', values=['Ranch']),
                              FeatureRow(label='Heating', values=['Forced Air']),
                              FeatureRow(label='Parking', values=['Garage Spaces 1', 'Parking Total 2']),
                              FeatureRow(label='Age', values=['Built 2000'])],
                room_sections=[RoomSection(title='Main', area_text='1,200 sq. ft.', rooms=[
                    RoomEntry(name='Living Room', size="12 x 14"),
                    RoomEntry(name='Kitchen', size="10 x 12"),
                    RoomEntry(name='Bedroom', size="10 x 10")])])
        elif schema is ImageBatchAnalysis:
            names = [item['text'][6:] for item in content if item.get('text', '').startswith('FILE: ')]
            result = ImageBatchAnalysis(images=[ImageInsight(
                file_name=name, scene_type=SCENES[int(name[:2])], room_type=SCENES[int(name[:2])],
                caption=f'Fixture {name[:2]}.', showcase_score=10 - int(name[:2]) % 5,
                front_exterior=name.startswith('00'), exterior=int(name[:2]) in (0, 6, 7),
                avoid=name.startswith('11')) for name in reversed(names)])
        elif schema is BrochureCopyResponse:
            self.selected_files = [line.split(' | ')[0][2:] for line in content[0]['text'].splitlines() if line.startswith('- ')]
            # Reversed order proves captions follow filename, not response order.
            result = BrochureCopyResponse(title='Fictional Demo Home', summary_bullets=[
                'This fictional home provides three bedrooms and two bathrooms in a simple single floor layout.',
                'The sample listing identifies forced air heating and a single garage with an additional parking space.',
                'Generated color panels stand in for listing photos so this acceptance run contains no private property data.',
                'The demonstration measures a total of 1,200 square feet, matching the fictional source listing provided here.',
                'This synthetic brochure tests assembly and formatting, and is not an advertisement for a real property.'
            ], captions=[CaptionItem(file_name=name, caption=fixture_caption(name)) for name in reversed(self.selected_files)])
        else:
            raise AssertionError(f'Unexpected model schema: {schema}')
        return SimpleNamespace(output_parsed=result)


def verify_deck(output: Path, images: Path, client: FakeClient) -> dict:
    presentation = Presentation(output)
    text = '\n'.join(shape.text for slide in presentation.slides for shape in slide.shapes if shape.has_text_frame)
    assert '{{' not in text, 'Unfilled text placeholder'
    for expected in ['123 Example Ave', '$450,000', 'Fictional Demo Home', '3 Bedrooms', '2 Baths', '1,200']:
        assert expected in text, f'Missing mapped field: {expected}'
    pictures = [shape for slide in presentation.slides for shape in slide.shapes if shape.shape_type == 13]
    source_hashes = {hashlib.sha256(p.read_bytes()).hexdigest(): p.name for p in images.glob('*.png')}
    inserted = [source_hashes[hashlib.sha256(p.image.blob).hexdigest()] for p in pictures if hashlib.sha256(p.image.blob).hexdigest() in source_hashes]
    assert len(inserted) == 11 and len(set(inserted)) == 11, inserted
    assert set(inserted) == {'00_front_exterior.png', *client.selected_files}
    assert not any(name.startswith('11') for name in inserted)
    template = Presentation(ROOT / 'Brochure_Template.pptx')
    # Match embedded bytes to each original named slot's exact geometry.
    slots = {s.name.strip(): (n, s.left, s.top, s.width, s.height)
             for n, slide in enumerate(template.slides) for s in slide.shapes
             if s.name.strip().startswith('{{IMG_')}
    expected_slots = {'{{IMG_MAIN}}': '00_front_exterior.png',
                      **{f'{{{{IMG_{i}}}}}': name for i, name in enumerate(client.selected_files, 1)}}
    for slot, name in expected_slots.items():
        n, left, top, width, height = slots[slot]
        picture = next(s for s in presentation.slides[n].shapes
                       if s.shape_type == 13 and (s.left, s.top, s.width, s.height) == (left, top, width, height))
        assert source_hashes[hashlib.sha256(picture.image.blob).hexdigest()] == name
    for name in client.selected_files:
        assert fixture_caption(name) in text
    for i, name in enumerate(client.selected_files, 1):
        token = f'{{{{caption_{i}}}}}'
        n, original = next((n, s) for n, slide in enumerate(template.slides)
                           for s in slide.shapes if s.has_text_frame and token in s.text)
        filled = next(s for s in presentation.slides[n].shapes
                      if s.has_text_frame and (s.left, s.top) == (original.left, original.top))
        assert filled.text.strip() == fixture_caption(name)
    for p in pictures:
        assert 0 <= p.crop_left < .5 and 0 <= p.crop_right < .5
        assert 0 <= p.crop_top < .5 and 0 <= p.crop_bottom < .5
    return {'slides': len(presentation.slides), 'inserted_photos': inserted,
            'gallery_order': client.selected_files, 'mock_calls': client.calls,
            'remaining_placeholders': 0, 'network_calls': 0}


def run_acceptance(directory: Path) -> Path:
    pdf, images = create_fixtures(directory)
    template = create_demo_template(directory)
    output = directory / 'acceptance.pptx'
    client = FakeClient()
    with patch('brochure_agent.pipeline.build_client', return_value=client), \
         patch.object(socket.socket, 'connect', side_effect=AssertionError('Network forbidden in offline acceptance')):
        result = main(['--cli', '--pdf', str(pdf), '--images', str(images),
                       '--template', str(template), '--output', str(output)])
    assert result == 0, f'CLI failed with status {result}'
    report = verify_deck(output, images, client)
    assert client.calls == ['ListingExtraction', 'ImageBatchAnalysis', 'ImageBatchAnalysis', 'BrochureCopyResponse']
    (directory / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'outputs' / 'offline-acceptance')
    run_acceptance(parser.parse_args().output_dir)
