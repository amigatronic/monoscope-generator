# -*- coding: utf-8 -*-
"""
Convert SVG into a portable parametric vector intermediate representation.

SVG-specific transforms are resolved during conversion. The resulting JSON
contains only normalized geometry, styles, gradients, and clip geometry.
Curves remain parametric; they are not flattened to a fixed raster resolution.

The output is intended to be a stable interchange format for future target
backends such as Python, C++, AMOS, and 68000 assembly.
"""
import json
import math
import os
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

from svg_geometry_core import (
    IDENTITY_TRANSFORM,
    apply_transform,
    compose_transform,
    normalize_gradient,
    normalize_shape_geometry,
    parse_clip_paths,
    parse_css_styles,
    parse_gradients,
    parse_path_segments,
    parse_points,
    parse_transform,
    resolve_style,
    _shape_bbox,
    _tag,
)

IR_VERSION = 2


def _float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_viewbox(root):
    value = root.get("viewBox")
    if not value:
        return None
    values = [
        float(item)
        for item in re.split(r"[\s,]+", value.strip())
        if item.strip()
    ]
    if len(values) != 4:
        return None
    x, y, width, height = values
    return {"xmin": x, "ymin": y, "xmax": x + width, "ymax": y + height}


def _parse_document_bounds(root):
    viewbox = _parse_viewbox(root)
    if viewbox:
        return viewbox

    width = re.sub(r"(?:px|pt|pc|in|cm|mm)$", "", root.get("width", "800"), flags=re.I)
    height = re.sub(r"(?:px|pt|pc|in|cm|mm)$", "", root.get("height", "600"), flags=re.I)
    return {
        "xmin": 0.0,
        "ymin": 0.0,
        "xmax": _float(width, 800.0),
        "ymax": _float(height, 600.0),
    }


def _is_visible(style):
    return (
        str(style.get("display", "inline")).strip().lower() != "none"
        and str(style.get("visibility", "visible")).strip().lower() != "hidden"
    )


def _stroke_width(style):
    value = style.get("stroke-width", 1.0)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower()
    if text.endswith("px"):
        text = text[:-2]
    try:
        return float(text)
    except ValueError:
        return 1.0


def _opacity(style):
    def number(key):
        try:
            return float(style.get(key, 1.0))
        except (TypeError, ValueError):
            return 1.0

    return max(0.0, min(1.0, number("opacity") * number("fill-opacity")))


def _stroke_opacity(style):
    try:
        return max(0.0, min(1.0, float(style.get("opacity", 1.0)) * float(style.get("stroke-opacity", 1.0))))
    except (TypeError, ValueError):
        return 1.0


def _raw_shape(elem):
    tag = _tag(elem)
    if tag == "rect":
        shape = {
            "type": "rect",
            "x": _float(elem.get("x")),
            "y": _float(elem.get("y")),
            "w": _float(elem.get("width")),
            "h": _float(elem.get("height")),
        }
        if elem.get("rx") is not None:
            shape["rx"] = _float(elem.get("rx"))
        if elem.get("ry") is not None:
            shape["ry"] = _float(elem.get("ry"))
        return shape

    if tag == "circle":
        r = _float(elem.get("r"))
        return {
            "type": "ellipse",
            "cx": _float(elem.get("cx")),
            "cy": _float(elem.get("cy")),
            "rx": r,
            "ry": r,
        }

    if tag == "ellipse":
        return {
            "type": "ellipse",
            "cx": _float(elem.get("cx")),
            "cy": _float(elem.get("cy")),
            "rx": _float(elem.get("rx")),
            "ry": _float(elem.get("ry")),
        }

    if tag == "line":
        return {
            "type": "line",
            "x1": _float(elem.get("x1")),
            "y1": _float(elem.get("y1")),
            "x2": _float(elem.get("x2")),
            "y2": _float(elem.get("y2")),
        }

    if tag in ("polyline", "polygon"):
        return {
            "type": "polygon",
            "points": parse_points(elem.get("points", "")),
            "closed": tag == "polygon",
        }

    if tag == "path":
        return {
            "type": "path",
            "segments": parse_path_segments(elem.get("d", "")),
        }

    return None


def _normalized_stroke_width(raw_width, transform):
    """Normalize stroke width without retaining an SVG transform.

    A uniform scale is exact. For a non-uniform affine transform, the
    geometric-mean scale preserves the overall stroke area reasonably while
    keeping the IR scalar and portable. Exact anisotropic stroke outlining can
    be added later as a dedicated geometry pass.
    """
    a, b, c, d, _, _ = transform
    determinant = abs(a * d - b * c)
    if determinant <= 0:
        return raw_width
    return raw_width * math.sqrt(determinant)


def _transform_user_space_clip(clip_definition, transform):
    """Apply the referencing element transform to a user-space clip."""
    if not clip_definition or clip_definition.get("units", "userSpaceOnUse") != "userSpaceOnUse":
        return clip_definition

    return {
        "units": "userSpaceOnUse",
        "shapes": [
            normalize_shape_geometry(item, transform)
            for item in clip_definition.get("shapes", [])
        ],
    }


def _normalized_clip_for_shape(clip_definition, shape_bbox):
    if not clip_definition:
        return None

    units = clip_definition.get("units", "userSpaceOnUse")
    if units == "userSpaceOnUse":
        return clip_definition

    x0, y0, x1, y1 = shape_bbox
    width = x1 - x0
    height = y1 - y0

    def map_shape(item):
        item = dict(item)
        if item.get("type") == "rect":
            item["x"] = x0 + item["x"] * width
            item["y"] = y0 + item["y"] * height
            item["w"] *= width
            item["h"] *= height
        elif item.get("type") == "ellipse":
            item["cx"] = x0 + item["cx"] * width
            item["cy"] = y0 + item["cy"] * height
            item["rx"] *= width
            item["ry"] *= height
        elif item.get("type") == "line":
            item["x1"] = x0 + item["x1"] * width
            item["y1"] = y0 + item["y1"] * height
            item["x2"] = x0 + item["x2"] * width
            item["y2"] = y0 + item["y2"] * height
        elif item.get("type") == "polygon":
            item["points"] = [[x0 + px * width, y0 + py * height] for px, py in item.get("points", [])]
        elif item.get("type") == "path":
            segments = []
            for seg in item.get("segments", []):
                out = dict(seg)
                for key in ("x", "x1", "x2"):
                    if key in out:
                        ykey = "y" if key == "x" else "y" + key[1:]
                        out[key] = x0 + out[key] * width
                        out[ykey] = y0 + out[ykey] * height
                if "rx" in out:
                    out["rx"] *= width
                if "ry" in out:
                    out["ry"] *= height
                segments.append(out)
            item["segments"] = segments
        return item

    return {
        "units": "userSpaceOnUse",
        "shapes": [map_shape(item) for item in clip_definition.get("shapes", [])],
    }


def _process_element(
    elem,
    css_dict,
    gradient_colors,
    gradient_defs,
    clip_paths,
    current_style,
    shapes,
    parent_transform=IDENTITY_TRANSFORM,
    active_clip=None,
):
    tag = _tag(elem)
    if tag in ("defs", "style", "title", "metadata", "clipPath"):
        return

    style = resolve_style(elem, css_dict, gradient_colors, gradient_defs, current_style)
    local_transform = parse_transform(elem.get("transform", ""))
    transform = compose_transform(parent_transform, local_transform)

    if not _is_visible(style):
        return

    clip_ref = style.get("clip-path", elem.get("clip-path", ""))
    clip = active_clip
    match = re.fullmatch(r"url\(\s*#([\w-]+)\s*\)", str(clip_ref).strip()) if clip_ref else None
    if match and match.group(1) in clip_paths:
        clip_definition = clip_paths[match.group(1)]
        clip = _transform_user_space_clip(clip_definition, transform)

    raw = _raw_shape(elem)
    if raw is not None:
        geometry = normalize_shape_geometry(raw, transform)
        bbox = _shape_bbox(geometry)
        fill = style.get("fill", "black")
        stroke = style.get("stroke", "none")

        if isinstance(fill, dict):
            fill = normalize_gradient(fill, bbox)
        if isinstance(stroke, dict):
            stroke = normalize_gradient(stroke, bbox)

        normalized_clip = _normalized_clip_for_shape(clip, bbox)
        shape = {
            **geometry,
            "fill": fill,
            "stroke": stroke,
            "stroke_width": _stroke_width(style),
            "fill_opacity": _opacity(style),
            "stroke_opacity": _stroke_opacity(style),
            "clip": normalized_clip,
        }
        shapes.append(shape)

    for child in elem:
        _process_element(
            child,
            css_dict,
            gradient_colors,
            gradient_defs,
            clip_paths,
            style,
            shapes,
            parent_transform=transform,
            active_clip=clip,
        )


def svg_to_json(svg_file_path, output_json_path):
    tree = ET.parse(svg_file_path)
    root = tree.getroot()

    css_dict = parse_css_styles(root)
    gradient_colors, gradient_defs = parse_gradients(root)
    clip_paths = parse_clip_paths(root)
    bounds = _parse_document_bounds(root)

    shapes = []
    base_style = {
        "fill": "black",
        "stroke": "none",
        "stroke-width": "1",
        "fill-opacity": "1",
        "stroke-opacity": "1",
        "opacity": "1",
        "display": "inline",
        "visibility": "visible",
    }

    root_transform = parse_transform(root.get("transform", ""))
    for child in root:
        _process_element(
            child,
            css_dict,
            gradient_colors,
            gradient_defs,
            clip_paths,
            base_style,
            shapes,
            parent_transform=root_transform,
        )

    document = {
        "format": "portable_vector_ir",
        "version": IR_VERSION,
        "source": os.path.basename(svg_file_path),
        "bounds": bounds,
        "shapes": shapes,
    }

    json_text = json.dumps(
        document,
        indent=2,
        ensure_ascii=False,
    )
    json_text = json_text.replace("\r\n", "\r\n").replace("\r\n", "\r\n")
    json_text = json_text.replace("\r\n", "\r\n") + "\r\n"
    Path(output_json_path).write_bytes(json_text.encode("utf-8"))

    print(f"Generated: {output_json_path} ({len(shapes)} shapes, IR v{IR_VERSION})")


if __name__ == "__main__":
    if len(sys.argv) == 3:
        svg_to_json(sys.argv[1], sys.argv[2])
    elif len(sys.argv) == 1:
        import tkinter as tk
        from tkinter import filedialog, messagebox

        root = tk.Tk()
        root.withdraw()

        input_svg = filedialog.askopenfilename(
            title="Select SVG file",
            filetypes=[
                ("SVG files", "*.svg"),
                ("All files", "*.*"),
            ],
        )

        if input_svg:
            input_path = Path(input_svg)
            output_path = input_path.with_suffix(".json")

            if output_path.exists():
                overwrite = messagebox.askyesno(
                    "File already exists",
                    f"The file already exists:\r\n\r\n{output_path.name}\r\n\r\n"
                    "Do you want to overwrite it?",
                    parent=root,
                )
            else:
                overwrite = True

            if overwrite:
                try:
                    svg_to_json(str(input_path), str(output_path))
                    messagebox.showinfo(
                        "Conversion completed",
                        f"Portable Vector IR JSON created successfully:\r\n\r\n"
                        f"{output_path}",
                        parent=root,
                    )
                except Exception as exc:
                    messagebox.showerror(
                        "Conversion failed",
                        f"Unable to convert the SVG file.\r\n\r\n{exc}",
                        parent=root,
                    )

        root.destroy()
    else:
        print("Usage: python svg_to_json.py [input.svg output.json]")
