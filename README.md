# svg-vector-ir

Convert SVG files into a **Portable Vector IR** (intermediate representation): a flat JSON document with normalized geometry, styles, gradients and clip paths. All SVG transforms are resolved at conversion time, so target backends never need to implement SVG transform semantics. Curves stay parametric (they are not flattened to a fixed resolution).

The IR is meant as a stable interchange format for future backends (Python, C++, AMOS, 68000 assembly). This repository includes the converter and three consumers: a PDF exporter, a Pillow reference renderer and a browser viewer.

```
            svg_to_json.py                 json_to_pdf.py      -> vector PDF
 SVG  ------------------------>  JSON IR  render_reference.py -> PNG
   (uses svg_geometry_core.py)             monoscope_viewer.html -> browser
```

## Files

| File | Purpose |
|------|---------|
| `svg_to_json.py` | SVG -> Portable Vector IR v2 (CLI, or file dialogs when run without arguments) |
| `svg_geometry_core.py` | Parsing/normalization core (transforms, paths, arcs to cubics, CSS styles, gradients, clip paths). Standard library only |
| `json_to_pdf.py` | IR -> vector PDF via ReportLab |
| `render_reference.py` | IR -> PNG reference rasterizer via Pillow (optional supersampling) |
| `monoscope_viewer.html` | Standalone SVG-based viewer for IR JSON files (pan, zoom, fit, 100%) |
| `examples/` | Sample: Philips PM5544 test pattern (SVG, JSON, PNG render) |

## Requirements

- Python 3.8+
- `svg_to_json.py` / `svg_geometry_core.py`: no third-party packages
- `json_to_pdf.py`: [ReportLab](https://pypi.org/project/reportlab/)
- `render_reference.py`: [Pillow](https://pypi.org/project/Pillow/)
- Optional GUI mode (`svg_to_json.py`, `render_reference.py` without arguments): `tkinter` (on Debian/Ubuntu: `sudo apt install python3-tk`)

```bash
pip install -r requirements.txt
```

## Usage

```bash
# SVG -> JSON IR
python svg_to_json.py input.svg output.json

# JSON IR -> PDF (optional scale factor, default 1.0)
python json_to_pdf.py input.json output.pdf [scale]

# JSON IR -> PNG (width in pixels, default 1200; supersample factor, default 1)
python render_reference.py input.json output.png [width] [supersample]
```

Running `svg_to_json.py` or `render_reference.py` with no arguments opens file dialogs instead.

### Viewer

Open `monoscope_viewer.html` in a browser. It tries to load `Philips_PM5544.json` from the same folder (this requires serving over HTTP, e.g. `python -m http.server`); otherwise use the file picker to load any IR JSON. Mouse wheel zooms, drag pans.

## IR format (v2)

```json
{
  "format": "portable_vector_ir",
  "version": 2,
  "source": "file.svg",
  "bounds": {"xmin": 0, "ymin": 0, "xmax": 768, "ymax": 576},
  "shapes": [ { "type": "...", "fill": ..., "stroke": ..., "stroke_width": 1,
                "fill_opacity": 1, "stroke_opacity": 1, "clip": null } ]
}
```

Shape types: `rect`, `ellipse`, `line`, `polygon` (with a `closed` flag), `path` (`moveto`, `lineto`, `cubic`, `quad`, `arc`, `close`). Fills and strokes are a color string, `"none"`, or a gradient object (`linear_gradient` / `radial_gradient`) in document coordinates. Clips are lists of user-space shapes.

## Supported / not supported

Supported SVG elements: `rect`, `circle`, `ellipse`, `line`, `polyline`, `polygon`, `path`, with `transform`, inherited/CSS styles, linear and radial gradients, clip paths, opacity, `display`/`visibility`.

Not supported: `text`, `image`, `use`, filters, masks, patterns, stroke dashes/caps/joins. Non-uniform transforms are approximated in stroke width (geometric-mean scale).

Consumer limits: `json_to_pdf.py` renders linear gradients only; `render_reference.py` and the viewer handle linear and radial.

## License

The source code is licensed under GPL-3.0. See [LICENSE](LICENSE). Copyright (C) 2026 amigatronic.

## Attribution

The sample test pattern (`examples/Philips_PM5544.svg`, and the derived `Philips_PM5544.json` and `Philips_PM5544_render.png`, including the JSON copy in the repository root used by the viewer) is *Philips PM5544.svg* by Tucvbif, from [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Philips_PM5544.svg), licensed under [CC BY 2.5](https://creativecommons.org/licenses/by/2.5/). The JSON and PNG were generated from the SVG with the tools in this repository. See [examples/ATTRIBUTION.md](examples/ATTRIBUTION.md).
