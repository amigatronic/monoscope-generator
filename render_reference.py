# -*- coding: utf-8 -*-
"""
Reference renderer for Portable Vector IR v2.

This renderer reads the JSON produced by svg_to_json.py and rasterizes the
normalized vector geometry using Pillow. Geometry remains parametric in the
JSON; curves are flattened dynamically here according to the requested
rendering resolution.

The renderer is intentionally independent from SVG parsing. It only consumes
Portable Vector IR v2.
"""

import json
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw


# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

def parse_color(value, alpha=1.0):
    if value is None or value == "none":
        return None

    if isinstance(value, str):
        text = value.strip().lower()
        if text == "none":
            return None
        if text.startswith("#"):
            text = text[1:]
        if len(text) == 3:
            text = "".join(ch * 2 for ch in text)
        if len(text) >= 6:
            try:
                r = int(text[0:2], 16)
                g = int(text[2:4], 16)
                b = int(text[4:6], 16)
                return (r, g, b, max(0, min(255, round(255 * alpha))))
            except ValueError:
                pass

    return (0, 0, 0, max(0, min(255, round(255 * alpha))))


def lerp_color(c0, c1, t):
    t = max(0.0, min(1.0, t))
    return (
        round(c0[0] + (c1[0] - c0[0]) * t),
        round(c0[1] + (c1[1] - c0[1]) * t),
        round(c0[2] + (c1[2] - c0[2]) * t),
        round(c0[3] + (c1[3] - c0[3]) * t),
    )


def gradient_color(stops, t, spread="pad"):
    if not stops:
        return (0, 0, 0, 255)

    t = float(t)

    if spread == "repeat":
        t = t % 1.0
    elif spread == "reflect":
        t = t % 2.0
        if t > 1.0:
            t = 2.0 - t
    else:
        t = max(0.0, min(1.0, t))

    prepared = []
    for stop in stops:
        color = parse_color(
            stop.get("color", "#000000"),
            stop.get("opacity", 1.0),
        )
        if color is not None:
            prepared.append((float(stop.get("offset", 0.0)), color))

    if not prepared:
        return (0, 0, 0, 255)

    if t <= prepared[0][0]:
        return prepared[0][1]
    if t >= prepared[-1][0]:
        return prepared[-1][1]

    for index in range(len(prepared) - 1):
        o0, c0 = prepared[index]
        o1, c1 = prepared[index + 1]
        if o0 <= t <= o1:
            local_t = 0.0 if o1 == o0 else (t - o0) / (o1 - o0)
            return lerp_color(c0, c1, local_t)

    return prepared[-1][1]


# ---------------------------------------------------------------------------
# Dynamic curve flattening
# ---------------------------------------------------------------------------

def _segments_for_length(p0, p1, px_per_unit, minimum=4, target_pixels=3.0):
    distance = math.hypot(
        (p1[0] - p0[0]) * px_per_unit,
        (p1[1] - p0[1]) * px_per_unit,
    )
    return max(minimum, min(512, int(distance / target_pixels) + 1))


def _cubic_points(p0, c1, c2, p1, count):
    points = []
    for index in range(1, count + 1):
        t = index / count
        mt = 1.0 - t
        points.append((
            mt**3 * p0[0]
            + 3 * mt**2 * t * c1[0]
            + 3 * mt * t**2 * c2[0]
            + t**3 * p1[0],
            mt**3 * p0[1]
            + 3 * mt**2 * t * c1[1]
            + 3 * mt * t**2 * c2[1]
            + t**3 * p1[1],
        ))
    return points


def _quad_points(p0, c, p1, count):
    points = []
    for index in range(1, count + 1):
        t = index / count
        mt = 1.0 - t
        points.append((
            mt**2 * p0[0] + 2 * mt * t * c[0] + t**2 * p1[0],
            mt**2 * p0[1] + 2 * mt * t * c[1] + t**2 * p1[1],
        ))
    return points


def _arc_center_parameters(x1, y1, rx, ry, phi_deg, large_arc, sweep, x2, y2):
    if abs(rx) < 1e-12 or abs(ry) < 1e-12:
        return None

    rx = abs(rx)
    ry = abs(ry)
    phi = math.radians(phi_deg % 360.0)

    dx = (x1 - x2) / 2.0
    dy = (y1 - y2) / 2.0
    cos_phi = math.cos(phi)
    sin_phi = math.sin(phi)

    xp = cos_phi * dx + sin_phi * dy
    yp = -sin_phi * dx + cos_phi * dy

    scale = (xp * xp) / (rx * rx) + (yp * yp) / (ry * ry)
    if scale > 1.0:
        factor = math.sqrt(scale)
        rx *= factor
        ry *= factor

    numerator = (
        rx * rx * ry * ry
        - rx * rx * yp * yp
        - ry * ry * xp * xp
    )
    denominator = (
        rx * rx * yp * yp
        + ry * ry * xp * xp
    )

    if denominator == 0:
        coefficient = 0.0
    else:
        sign = -1.0 if bool(large_arc) == bool(sweep) else 1.0
        coefficient = sign * math.sqrt(max(0.0, numerator / denominator))

    cxp = coefficient * (rx * yp / ry)
    cyp = coefficient * (-ry * xp / rx)

    cx = cos_phi * cxp - sin_phi * cyp + (x1 + x2) / 2.0
    cy = sin_phi * cxp + cos_phi * cyp + (y1 + y2) / 2.0

    def vector_angle(ax, ay, bx, by):
        denominator = math.hypot(ax, ay) * math.hypot(bx, by)
        if denominator == 0:
            return 0.0
        value = max(-1.0, min(1.0, (ax * bx + ay * by) / denominator))
        angle = math.acos(value)
        if ax * by - ay * bx < 0:
            angle = -angle
        return angle

    start = vector_angle(
        1.0,
        0.0,
        (xp - cxp) / rx,
        (yp - cyp) / ry,
    )
    delta = vector_angle(
        (xp - cxp) / rx,
        (yp - cyp) / ry,
        (-xp - cxp) / rx,
        (-yp - cyp) / ry,
    )

    if not sweep and delta > 0:
        delta -= 2.0 * math.pi
    elif sweep and delta < 0:
        delta += 2.0 * math.pi

    return cx, cy, rx, ry, phi, start, delta


def _arc_points(segment, px_per_unit):
    params = _arc_center_parameters(
        segment["x1"],
        segment["y1"],
        segment["rx"],
        segment["ry"],
        segment.get("phi", 0.0),
        segment.get("large_arc", False),
        segment.get("sweep", False),
        segment["x"],
        segment["y"],
    )

    if params is None:
        return [(segment["x"], segment["y"])]

    cx, cy, rx, ry, phi, start, delta = params
    radius_pixels = max(rx, ry) * px_per_unit
    count = max(8, min(1024, int(abs(delta) * radius_pixels / 3.0) + 1))

    points = []
    cos_phi = math.cos(phi)
    sin_phi = math.sin(phi)

    for index in range(1, count + 1):
        angle = start + delta * index / count
        ex = rx * math.cos(angle)
        ey = ry * math.sin(angle)
        points.append((
            cx + ex * cos_phi - ey * sin_phi,
            cy + ex * sin_phi + ey * cos_phi,
        ))

    return points


def flatten_path(segments, px_per_unit):
    polylines = []
    current = []
    current_point = (0.0, 0.0)
    subpath_start = (0.0, 0.0)

    for segment in segments:
        command = segment.get("cmd")

        if command == "moveto":
            if len(current) > 1:
                polylines.append(current)
            current_point = (segment["x"], segment["y"])
            subpath_start = current_point
            current = [current_point]

        elif command == "lineto":
            point = (segment["x"], segment["y"])
            current.append(point)
            current_point = point

        elif command == "cubic":
            p0 = current_point
            p1 = (segment["x"], segment["y"])
            count = _segments_for_length(p0, p1, px_per_unit)
            current.extend(_cubic_points(
                p0,
                (segment["x1"], segment["y1"]),
                (segment["x2"], segment["y2"]),
                p1,
                count,
            ))
            current_point = p1

        elif command == "quad":
            p0 = current_point
            p1 = (segment["x"], segment["y"])
            count = _segments_for_length(p0, p1, px_per_unit)
            current.extend(_quad_points(
                p0,
                (segment["x1"], segment["y1"]),
                p1,
                count,
            ))
            current_point = p1

        elif command == "arc":
            points = _arc_points(segment, px_per_unit)
            current.extend(points)
            current_point = (segment["x"], segment["y"])

        elif command == "close":
            if current and current[-1] != subpath_start:
                current.append(subpath_start)
            current_point = subpath_start

    if len(current) > 1:
        polylines.append(current)

    return polylines


def ellipse_points(cx, cy, rx, ry, px_per_unit):
    radius_pixels = max(abs(rx), abs(ry)) * px_per_unit
    count = max(24, min(1024, int(2.0 * math.pi * radius_pixels / 3.0) + 1))
    return [
        (
            cx + rx * math.cos(2.0 * math.pi * i / count),
            cy + ry * math.sin(2.0 * math.pi * i / count),
        )
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# Shape conversion
# ---------------------------------------------------------------------------

def shape_polylines(shape, px_per_unit):
    shape_type = shape.get("type")

    if shape_type == "polygon":
        points = [tuple(point) for point in shape.get("points", [])]
        if shape.get("closed") and points and points[-1] != points[0]:
            points.append(points[0])
        return [points] if len(points) > 1 else []

    if shape_type == "rect":
        x = shape["x"]
        y = shape["y"]
        w = shape["w"]
        h = shape["h"]
        points = [
            (x, y),
            (x + w, y),
            (x + w, y + h),
            (x, y + h),
            (x, y),
        ]
        return [points]

    if shape_type == "ellipse":
        points = ellipse_points(
            shape["cx"],
            shape["cy"],
            shape["rx"],
            shape["ry"],
            px_per_unit,
        )
        points.append(points[0])
        return [points]

    if shape_type == "line":
        return [[
            (shape["x1"], shape["y1"]),
            (shape["x2"], shape["y2"]),
        ]]

    if shape_type == "path":
        return flatten_path(shape.get("segments", []), px_per_unit)

    return []


def shape_is_closed(shape, polyline):
    if shape.get("type") in ("polygon", "rect", "ellipse"):
        return True
    return len(polyline) > 2 and polyline[0] == polyline[-1]


# ---------------------------------------------------------------------------
# Clip masks
# ---------------------------------------------------------------------------

def draw_clip_shape(draw, shape, px_per_unit, origin_x, origin_y):
    polylines = shape_polylines(shape, px_per_unit)
    for points in polylines:
        if len(points) >= 3:
            draw.polygon(
                [
                    (
                        (x - origin_x) * px_per_unit,
                        (y - origin_y) * px_per_unit,
                    )
                    for x, y in points
                ],
                fill=255,
            )


def build_clip_mask(clip, bbox, size, px_per_unit):
    if not clip:
        return None

    xmin, ymin, xmax, ymax = bbox
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)

    for shape in clip.get("shapes", []):
        draw_clip_shape(
            draw,
            shape,
            px_per_unit,
            xmin,
            ymin,
        )

    return mask


# ---------------------------------------------------------------------------
# Gradient rendering
# ---------------------------------------------------------------------------

def gradient_image(fill, bbox, size, px_per_unit):
    xmin, ymin, xmax, ymax = bbox
    width, height = size

    image = Image.new("RGBA", size)
    pixels = []

    gradient_type = fill.get("type")
    stops = fill.get("stops", [])
    spread = fill.get("spread", "pad")

    if gradient_type == "linear_gradient":
        x1 = fill.get("x1", 0.0)
        y1 = fill.get("y1", 0.0)
        x2 = fill.get("x2", 1.0)
        y2 = fill.get("y2", 0.0)

        dx = x2 - x1
        dy = y2 - y1
        denominator = dx * dx + dy * dy

        for py in range(height):
            y = ymin + (py + 0.5) / px_per_unit
            for px in range(width):
                x = xmin + (px + 0.5) / px_per_unit
                if denominator == 0:
                    t = 0.0
                else:
                    t = ((x - x1) * dx + (y - y1) * dy) / denominator
                pixels.append(gradient_color(stops, t, spread))

    elif gradient_type == "radial_gradient":
        cx = fill.get("cx", 0.0)
        cy = fill.get("cy", 0.0)
        r = max(abs(fill.get("r", 1.0)), 1e-12)

        for py in range(height):
            y = ymin + (py + 0.5) / px_per_unit
            for px in range(width):
                x = xmin + (px + 0.5) / px_per_unit
                t = math.hypot(x - cx, y - cy) / r
                pixels.append(gradient_color(stops, t, spread))

    else:
        color = parse_color(stops[0].get("color", "#000000")) if stops else (0, 0, 0, 255)
        pixels = [color] * (width * height)

    image.putdata(pixels)
    return image


# ---------------------------------------------------------------------------
# Main renderer
# ---------------------------------------------------------------------------

def render_json(json_path, output_path, output_width=1200, supersample=1):
    with open(json_path, "r", encoding="utf-8") as handle:
        document = json.load(handle)

    if document.get("format") != "portable_vector_ir":
        raise ValueError("Unsupported JSON format.")

    if int(document.get("version", 0)) != 2:
        raise ValueError("This renderer requires Portable Vector IR version 2.")

    bounds = document["bounds"]
    xmin = float(bounds["xmin"])
    ymin = float(bounds["ymin"])
    xmax = float(bounds["xmax"])
    ymax = float(bounds["ymax"])

    vector_width = xmax - xmin
    vector_height = ymax - ymin

    if vector_width <= 0 or vector_height <= 0:
        raise ValueError("Invalid document bounds.")

    supersample = max(1, int(supersample))
    final_width = max(1, int(output_width))
    final_height = max(
        1,
        round(final_width * vector_height / vector_width),
    )

    render_width = final_width * supersample
    render_height = final_height * supersample
    px_per_unit = render_width / vector_width

    image = Image.new(
        "RGBA",
        (render_width, render_height),
        (255, 255, 255, 255),
    )

    for shape in document.get("shapes", []):
        polylines = shape_polylines(shape, px_per_unit)
        if not polylines:
            continue

        shape_points = [
            point
            for polyline in polylines
            for point in polyline
        ]

        if not shape_points:
            continue

        shape_xmin = min(point[0] for point in shape_points)
        shape_ymin = min(point[1] for point in shape_points)
        shape_xmax = max(point[0] for point in shape_points)
        shape_ymax = max(point[1] for point in shape_points)

        stroke_width = float(shape.get("stroke_width", 1.0))
        margin = max(2.0, stroke_width * 2.0)

        local_bbox = (
            shape_xmin - margin,
            shape_ymin - margin,
            shape_xmax + margin,
            shape_ymax + margin,
        )

        local_width = max(
            1,
            math.ceil((local_bbox[2] - local_bbox[0]) * px_per_unit),
        )
        local_height = max(
            1,
            math.ceil((local_bbox[3] - local_bbox[1]) * px_per_unit),
        )

        layer = Image.new(
            "RGBA",
            (local_width, local_height),
            (0, 0, 0, 0),
        )
        draw = ImageDraw.Draw(layer)

        fill = shape.get("fill")
        fill_opacity = float(shape.get("fill_opacity", 1.0))
        stroke = shape.get("stroke")
        stroke_opacity = float(shape.get("stroke_opacity", 1.0))

        # Build the geometry mask first. This also supports gradients.
        geometry_mask = Image.new("L", layer.size, 0)
        mask_draw = ImageDraw.Draw(geometry_mask)

        closed_polylines = [
            polyline
            for polyline in polylines
            if shape_is_closed(shape, polyline)
        ]

        for polyline in closed_polylines:
            mask_draw.polygon(
                [
                    (
                        (x - local_bbox[0]) * px_per_unit,
                        (y - local_bbox[1]) * px_per_unit,
                    )
                    for x, y in polyline
                ],
                fill=round(255 * fill_opacity),
            )

        if isinstance(fill, dict):
            gradient = gradient_image(
                fill,
                local_bbox,
                layer.size,
                px_per_unit,
            )
            layer.alpha_composite(
                Image.composite(
                    gradient,
                    Image.new("RGBA", layer.size, (0, 0, 0, 0)),
                    geometry_mask,
                )
            )
        else:
            color = parse_color(fill, fill_opacity)
            if color is not None:
                fill_layer = Image.new("RGBA", layer.size, color)
                layer.alpha_composite(
                    Image.composite(
                        fill_layer,
                        Image.new("RGBA", layer.size, (0, 0, 0, 0)),
                        geometry_mask,
                    )
                )

        # Draw strokes after fills.
        stroke_color = parse_color(stroke, stroke_opacity)
        if stroke_color is not None:
            draw = ImageDraw.Draw(layer)
            width_px = max(
                1,
                round(stroke_width * px_per_unit),
            )
            for polyline in polylines:
                if len(polyline) < 2:
                    continue
                draw.line(
                    [
                        (
                            (x - local_bbox[0]) * px_per_unit,
                            (y - local_bbox[1]) * px_per_unit,
                        )
                        for x, y in polyline
                    ],
                    fill=stroke_color,
                    width=width_px,
                    joint="curve",
                )

        # Apply the normalized clip geometry, if present.
        clip = shape.get("clip")
        if clip:
            clip_mask = build_clip_mask(
                clip,
                local_bbox,
                layer.size,
                px_per_unit,
            )
            if clip_mask is not None:
                alpha = layer.getchannel("A")
                alpha = ImageChops.multiply(alpha, clip_mask)
                layer.putalpha(alpha)

        image.alpha_composite(
            layer,
            (
                round(local_bbox[0] * px_per_unit),
                round(local_bbox[1] * px_per_unit),
            ),
        )

    if supersample > 1:
        image = image.resize(
            (final_width, final_height),
            Image.Resampling.LANCZOS,
        )

    image.convert("RGB").save(output_path)
    print(
        f"Rendered: {output_path} "
        f"({final_width}x{final_height}, supersample={supersample})"
    )


# Import kept local to make the dependency obvious and easy to port.
from PIL import ImageChops


def main():
    if len(sys.argv) == 1:
        import tkinter as tk
        from tkinter import filedialog, messagebox, simpledialog

        root = tk.Tk()
        root.withdraw()

        json_path = filedialog.askopenfilename(
            title="Select Portable Vector IR JSON",
            filetypes=[
                ("JSON files", "*.json"),
                ("All files", "*.*"),
            ],
        )

        if not json_path:
            root.destroy()
            return

        width = simpledialog.askinteger(
            "Render width",
            "Output width in pixels:",
            initialvalue=1200,
            minvalue=64,
            maxvalue=20000,
            parent=root,
        )

        if width is None:
            root.destroy()
            return

        supersample = simpledialog.askinteger(
            "Supersampling",
            "Supersampling factor:",
            initialvalue=2,
            minvalue=1,
            maxvalue=4,
            parent=root,
        )

        if supersample is None:
            root.destroy()
            return

        input_path = Path(json_path)
        output_path = input_path.with_name(
            input_path.stem + "_render.png"
        )

        try:
            render_json(
                str(input_path),
                str(output_path),
                width,
                supersample,
            )
            messagebox.showinfo(
                "Render completed",
                f"Image created successfully:\\n\\n{output_path}",
                parent=root,
            )
        except Exception as exc:
            messagebox.showerror(
                "Render failed",
                f"Unable to render the JSON file.\\n\\n{exc}",
                parent=root,
            )
        finally:
            root.destroy()

        return

    if len(sys.argv) not in (3, 4, 5):
        print(
            "Usage: python render_reference.py "
            "input.json output.png [width] [supersample]"
        )
        sys.exit(1)

    input_json = sys.argv[1]
    output_png = sys.argv[2]
    width = int(sys.argv[3]) if len(sys.argv) >= 4 else 1200
    supersample = int(sys.argv[4]) if len(sys.argv) >= 5 else 1

    render_json(
        input_json,
        output_png,
        width,
        supersample,
    )


if __name__ == "__main__":
    main()
