# TODO

## IR / converter
- [ ] Support `use`, `text`, `image`, masks, patterns and filters.
- [ ] Stroke dash, line cap and line join properties.
- [ ] Exact stroke outlining for non-uniform transforms (currently geometric-mean scale).
- [ ] Add `pyproject.toml` and a proper package layout / entry points.

## Backends
- [ ] Radial gradients in `json_to_pdf.py`.
- [ ] Python, C++, AMOS and 68000 assembly backends (the stated goal of the IR).
- [ ] Add a code-generation backend for IR v2 (the old `Philips_PM5544_primitive.py` output is not reproducible from the current tools).

## Quality
- [ ] Unit tests (transform composition, arc conversion, gradient normalization).
- [ ] Regression test: render the sample and compare with `examples/Philips_PM5544_render.png`.
- [ ] Remove the no-op `replace("\r\n", "\r\n")` lines in `svg_to_json.py`.
- [ ] Viewer: drag-and-drop loading and embedded fallback when opened via `file://`.
