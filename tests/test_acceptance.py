import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from pptx import Presentation
from pptx.util import Inches
from pptx.dml.color import RGBColor

from brochure_agent.app import main
from brochure_agent.models import AppConfig
from brochure_agent.parsing import extract_pdf_text, iter_image_paths
from brochure_agent.pipeline import run_pipeline
from brochure_agent.rendering import replace_text_in_shape
from tests.offline_acceptance import ROOT, FakeClient, create_fixtures, run_acceptance, verify_deck, write_pdf


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        pdf, photos = create_fixtures(self.directory)
        self.config = AppConfig(pdf, photos, ROOT / 'Brochure_Template.pptx', self.directory / 'output.pptx')

    def test_real_cli_pipeline_and_supplied_template(self):
        with contextlib.redirect_stdout(io.StringIO()):
            output = run_acceptance(self.directory)
        self.assertTrue(output.is_file())

    def test_unmodified_supplied_template(self):
        client = FakeClient()
        with patch('brochure_agent.pipeline.build_client', return_value=client):
            run_pipeline(self.config, logger=lambda _: None)
        self.assertEqual(verify_deck(self.config.output_path, self.config.image_dir, client)['slides'], 2)

    def test_webp_photo_renders_through_pipeline_as_png(self):
        source = self.config.image_dir / '00_front_exterior.png'
        webp = source.with_suffix('.webp')
        with Image.open(source) as image:
            image.save(webp, format='WEBP', lossless=True)
        source.unlink()
        client = FakeClient()
        with patch('brochure_agent.pipeline.build_client', return_value=client):
            run_pipeline(self.config, logger=lambda _: None)
        template = Presentation(self.config.template_path)
        n, slot = next((n, shape) for n, slide in enumerate(template.slides)
                       for shape in slide.shapes if shape.name.strip() == '{{IMG_MAIN}}')
        output = Presentation(self.config.output_path)
        picture = next(shape for shape in output.slides[n].shapes
                       if shape.shape_type == 13 and
                       (shape.left, shape.top, shape.width, shape.height) ==
                       (slot.left, slot.top, slot.width, slot.height))
        self.assertEqual(picture.image.content_type, 'image/png')
        with Image.open(webp) as expected, Image.open(io.BytesIO(picture.image.blob)) as actual:
            self.assertEqual(actual.size, expected.size)
            self.assertEqual(actual.convert('RGB').tobytes(), expected.convert('RGB').tobytes())
        self.assertEqual(client.calls, ['ListingExtraction', 'ImageBatchAnalysis',
                                        'ImageBatchAnalysis', 'BrochureCopyResponse'])

    def test_missing_photos_fail_before_client_creation(self):
        for image in self.config.image_dir.iterdir():
            image.unlink()
        with patch('brochure_agent.pipeline.build_client') as client:
            with self.assertRaisesRegex(RuntimeError, 'No supported images'):
                run_pipeline(self.config, logger=lambda _: None)
            client.assert_not_called()

    def test_corrupt_photo_fails_before_client_creation(self):
        (self.config.image_dir / 'broken.png').write_bytes(b'not an image')
        with patch('brochure_agent.pipeline.build_client') as client:
            with self.assertRaisesRegex(RuntimeError, 'Cannot read image.*broken.png'):
                run_pipeline(self.config, logger=lambda _: None)
            client.assert_not_called()

    def test_unreadable_pdf_has_clear_error_and_no_client(self):
        self.config.pdf_path.write_bytes(b'not a pdf')
        with patch('brochure_agent.pipeline.build_client') as client:
            with self.assertRaisesRegex(RuntimeError, 'Cannot read listing PDF'):
                run_pipeline(self.config, logger=lambda _: None)
            client.assert_not_called()

    def test_image_only_pdf_has_clear_error(self):
        write_pdf(self.config.pdf_path, '')
        with self.assertRaisesRegex(RuntimeError, 'No text could be extracted'):
            extract_pdf_text(self.config.pdf_path)

    def test_incomplete_template_fails_before_client_creation(self):
        p = Presentation(self.config.template_path)
        shape = next(s for slide in p.slides for s in slide.shapes if s.name.strip() == '{{IMG_MAIN}}')
        shape.name = 'Renamed by accident'
        self.config.template_path = self.directory / 'incomplete.pptx'
        p.save(self.config.template_path)
        with patch('brochure_agent.pipeline.build_client') as client:
            with self.assertRaisesRegex(RuntimeError, 'Template missing.*IMG_MAIN'):
                run_pipeline(self.config, logger=lambda _: None)
            client.assert_not_called()

    def test_unknown_placeholder_fails_before_client_creation(self):
        p = Presentation(self.config.template_path)
        p.slides[0].shapes.add_textbox(0, 0, Inches(2), Inches(1)).text = '{{UNKNOWN}}'
        self.config.template_path = self.directory / 'unknown.pptx'
        p.save(self.config.template_path)
        with patch('brochure_agent.pipeline.build_client') as client:
            with self.assertRaisesRegex(RuntimeError, 'Unknown template placeholder.*UNKNOWN'):
                run_pipeline(self.config, logger=lambda _: None)
            client.assert_not_called()

    def test_sparse_gallery_removes_unused_template_pictures(self):
        for image in self.config.image_dir.iterdir():
            if not image.name.startswith(('00', '01')):
                image.unlink()
        with patch('brochure_agent.pipeline.build_client', return_value=FakeClient()):
            run_pipeline(self.config, logger=lambda _: None)
        p = Presentation(self.config.output_path)
        self.assertFalse(any(s.name.strip().startswith('{{IMG_') for slide in p.slides for s in slide.shapes))
        covers = [s for slide in p.slides for s in slide.shapes if s.name == 'Unused gallery slot']
        self.assertEqual(len(covers), 9)
        self.assertTrue(all(s.fill.fore_color.rgb == RGBColor(255, 255, 255) for s in covers))

    def test_fragmented_placeholder_replaced_with_style(self):
        p = Presentation()
        slide = p.slides.add_slide(p.slide_layouts[6])
        shape = slide.shapes.add_textbox(0, 0, Inches(3), Inches(1))
        paragraph = shape.text_frame.paragraphs[0]
        for text in ('Address: {{AD', 'DRE', 'SS}}'):
            run = paragraph.add_run()
            run.text = text
            run.font.bold = True
        replace_text_in_shape(shape, {'{{ADDRESS}}': '123 Example Ave'})
        self.assertEqual(shape.text, 'Address: 123 Example Ave')
        self.assertTrue(paragraph.runs[0].font.bold)

    def test_image_scan_ignores_directories_with_image_extension(self):
        (self.config.image_dir / 'folder.png').mkdir()
        self.assertTrue(all(p.is_file() for p in iter_image_paths(self.config.image_dir)))

    def test_cli_missing_required_arguments(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['--cli']), 2)

    def test_cli_input_failure_has_nonzero_exit(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            result = main(['--cli', '--pdf', str(self.directory / 'missing.pdf'), '--images', str(self.config.image_dir)])
        self.assertEqual(result, 1)
        self.assertIn('PDF not found', err.getvalue())
