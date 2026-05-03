# Brochure-Agent

Desktop helper for turning a messy real-estate listing PDF plus a folder of house photos into a filled-in brochure PowerPoint.

## What it does

- Extracts listing facts from a PDF with OpenAI structured output
- Ranks listing photos, picks a main exterior shot, and writes captions
- Generates a brochure title
- Fills `Brochure_Template.pptx` placeholders and image slots

## Template support

The current template is wired for these text placeholders:

- `{{ADDRESS}}`
- `{{PRICE}}`
- `{{BEDS_AND_BATHS}}`
- `{{TITLE}}`
- `{{SUMMARY}}`
- `{{FEATURE_BLOCK}}`
- `{{ROOM_BLOCK}}`
- `{{TOTAL}}`
- `{{caption_1}}` through `{{caption_10}}`

And these image placeholders by shape name:

- `{{IMG_MAIN}}`
- `{{IMG_1}}` through `{{IMG_10}}`

## Setup

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

Tkinter is included with the normal Windows Python installer. On Linux, install the OS Tk package if `python main.py` falls back to CLI mode.

Examples:

```bash
# Ubuntu/Debian
sudo apt install python3-tk

# Arch/EndeavourOS
sudo pacman -S tk
```

3. Set your API key:

```bash
export OPENAI_API_KEY=your_key_here
```

Optional:

```bash
export OPENAI_BASE_URL=https://your-compatible-endpoint
```

## Run

If your Python install includes Tk, the app opens a simple local desktop UI:

```bash
python main.py
```

On Windows, `run_gui.pyw` can also be launched with Python to open the GUI without needing command-line arguments.

The GUI lets you choose:

- Listing PDF
- Image folder
- PowerPoint template
- Output `.pptx` location
- OpenAI model

If Tk is unavailable, or if you want command-line mode:

```bash
python main.py --cli \
  --pdf "/path/to/listing.pdf" \
  --images "/path/to/image-folder" \
  --template "Brochure_Template.pptx" \
  --output "/path/to/output-brochure.pptx"
```

Default model: `gpt-5.5`

## Notes

- The app uses strict structured output for listing extraction and image analysis.
- `{{ADDRESS}}` in the template is split across PowerPoint XML runs, so the filler handles fragmented placeholders.
- The feature block and room block are rendered with custom formatting rather than plain string replacement.
- The current selection logic chooses one main image and up to ten gallery images.
