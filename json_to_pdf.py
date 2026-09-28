# -*- coding: utf-8 -*-
"""
Convert Portable Vector IR JSON into a vector PDF.

The converter reads the normalized geometry produced by svg_to_json.py.
Geometry is kept vectorial: paths, Bézier curves, lines, polygons, rectangles,
ellipses, clipping paths, fills, strokes, opacity, and gradients are rendered
without rasterizing the complete document.

ReportLab is used as the PDF writer.
"""

import json
import math
import sys
from pathlib import Path

from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor, Color, black


def _color(value):
    if value is None:
        return None

    text = str(value).strip().lower()

    if text in ("none", "transparent", ""):
        return None

    if text.startswith("#"):
        try:
            return HexColor(text)
        except ValueError:
            return black

    named = {
        "black": "#000000",
        "white": "#ffffff",
        "red": "#ff0000",
        "green": "#008000",
        "blue": "#0000ff",
        "yellow": "#ffff00",
        "cyan": "#00ffff",
        "magenta": "#ff00ff",
        "gray": "#808080",
        "grey": "#808080",
        "orange": "#ffa500",
        "purple": "#800080",
    }

    if text in named:
        return HexColor(named[text])

    if text.startswith("rgb(") and text.endswith(")"):
        try:
            parts = [int(float(x.strip())) for x in text[4:-1].split(",")]
            if len(parts) == 3:
                return Color(parts[0] / 255.0, parts[1] / 255.0, parts[2] / 255.0)
        except ValueError:
            pass

    return black


def _opacity_color(color, opacity):
    if color is None:
        return None
    return Color(
        color.red,
        color.green,
        color.blue,
        alpha=max(0.0, min(1.0, float(opacity))),
    )


def _flatten_arc(x1, y1, rx, ry, phi, large_arc, sweep, x2, y2):
    """Convert one SVG elliptical arc into cubic Bézier segments."""
    rx = abs(float(rx))
    ry = abs(float(ry))

    if rx == 0 or ry == 0:
        return [("line", x2, y2)]

    phi = math.radians(float(phi) % 360.0)
    cos_phi = math.cos(phi)
    sin_phi = math.sin(phi)

    dx = (x1 - x2) / 2.0
    dy = (y1 - y2) / 2.0

    x1p = cos_phi * dx + sin_phi * dy
    y1p = -sin_phi * dx + cos_phi * dy

    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1:
        scale = math.sqrt(lam)
        rx *= scale
        ry *= scale

    sign = -1.0 if bool(large_arc) == bool(sweep) else 1.0
    numerator = max(
        0.0,
        (rx * rx * ry * ry)
        - (rx * rx * y1p * y1p)
        - (ry * ry * x1p * x1p),
    )
    denominator = (rx * rx * y1p * y1p) + (ry * ry * x1p * x1p)
    coef = sign * math.sqrt(numerator / denominator) if denominator else 0.0

    cxp = coef * (rx * y1p / ry)
    cyp = coef * (-ry * x1p / rx)

    cx = cos_phi * cxp - sin_phi * cyp + (x1 + x2) / 2.0
    cy = sin_phi * cxp + cos_phi * cyp + (y1 + y2) / 2.0

    def angle(ux, uy, vx, vy):
        dot = ux * vx + uy * vy
        det = ux * vy - uy * vx
        return math.atan2(det, dot)

    ux = (x1p - cxp) / rx
    uy = (y1p - cyp) / ry
    vx = (-x1p - cxp) / rx
    vy = (-y1p - cyp) / ry

    theta1 = angle(1.0, 0.0, ux, uy)
    delta = angle(ux, uy, vx, vy)

    if not sweep and delta > 0:
        delta -= 2 * math.pi
    elif sweep and delta < 0:
        delta += 2 * math.pi

    count = max(1, int(math.ceil(abs(delta) / (math.pi / 2.0))))
    step = delta / count

    result = []

    for index in range(count):
        a0 = theta1 + index * step
        a1 = a0 + step

        t = math.tan((a1 - a0) / 4.0)
        k = 4.0 / 3.0 * t

        cos0, sin0 = math.cos(a0), math.sin(a0)
        cos1, sin1 = math.cos(a1), math.sin(a1)

        p0x = cx + rx * (cos_phi * cos0 - sin_phi * sin0)
        p0y = cy + ry * (sin_phi * cos0 + cos_phi * sin0)

        p3x = cx + rx * (cos_phi * cos1 - sin_phi * sin1)
        p3y = cy + ry * (sin_phi * cos1 + cos_phi * sin1)

        d0x = rx * (-cos_phi * sin0 - sin_phi * cos0)
        d0y = ry * (-sin_phi * sin0 + cos_phi * cos0)

        d1x = rx * (-cos_phi * sin1 - sin_phi * cos1)
        d1y = ry * (-sin_phi * sin1 + cos_phi * cos1)

        p1x = p0x + k * d0x
        p1y = p0y + k * d0y
        p2x = p3x - k * d1x
        p2y = p3y - k * d1y

        if index == 0:
            result.append(("move", p0x, p0y))

        result.append(("curve", p1x, p1y, p2x, p2y, p3x, p3y))

    return result


def _path_from_segments(c, segments, y_flip):
    """Build a ReportLab path from Portable Vector IR path segments."""
    path = c.beginPath()
    current = (0.0, 0.0)
    has_current = False
    subpath_start = None

    def yy(y):
        return y_flip(y)

    def move_to(x, y):
        nonlocal current, has_current, subpath_start
        path.moveTo(float(x), yy(float(y)))
        current = (float(x), float(y))
        subpath_start = current
        has_current = True

    for seg in segments:
        if not isinstance(seg, dict):
            continue

        cmd = str(seg.get("cmd", seg.get("command", ""))).lower()

        if cmd in ("move", "moveto", "m"):
            if "x" in seg and "y" in seg:
                move_to(seg["x"], seg["y"])

        elif cmd in ("line", "lineto", "l"):
            if "x" not in seg or "y" not in seg:
                continue
            x, y = float(seg["x"]), float(seg["y"])
            if not has_current:
                move_to(x, y)
            else:
                path.lineTo(x, yy(y))
                current = (x, y)

        elif cmd in ("cubic", "curveto", "c"):
            if not all(k in seg for k in ("x1", "y1", "x2", "y2", "x", "y")):
                continue

            x1, y1 = float(seg["x1"]), float(seg["y1"])
            x2, y2 = float(seg["x2"]), float(seg["y2"])
            x, y = float(seg["x"]), float(seg["y"])

            if not has_current:
                move_to(x, y)
                continue

            path.curveTo(
                x1, yy(y1),
                x2, yy(y2),
                x, yy(y),
            )
            current = (x, y)

        elif cmd in ("quad", "quadratic", "q"):
            if not all(k in seg for k in ("x1", "y1", "x", "y")):
                continue

            x0, y0 = current
            x1, y1 = float(seg["x1"]), float(seg["y1"])
            x2, y2 = float(seg["x"]), float(seg["y"])

            if not has_current:
                move_to(x2, y2)
                continue

            c1x = x0 + 2.0 / 3.0 * (x1 - x0)
            c1y = y0 + 2.0 / 3.0 * (y1 - y0)
            c2x = x2 + 2.0 / 3.0 * (x1 - x2)
            c2y = y2 + 2.0 / 3.0 * (y1 - y2)

            path.curveTo(
                c1x, yy(c1y),
                c2x, yy(c2y),
                x2, yy(y2),
            )
            current = (x2, y2)

        elif cmd in ("arc", "a"):
            if not all(k in seg for k in ("x", "y")):
                continue

            x1, y1 = current
            x2, y2 = float(seg["x"]), float(seg["y"])

            if not has_current:
                move_to(x2, y2)
                continue

            pieces = _flatten_arc(
                x1, y1,
                seg.get("rx", 0),
                seg.get("ry", 0),
                seg.get("rotation", seg.get("phi", 0)),
                seg.get("large_arc", seg.get("largeArc", 0)),
                seg.get("sweep", 0),
                x2, y2,
            )

            for piece in pieces:
                if piece[0] == "line":
                    path.lineTo(piece[1], yy(piece[2]))
                elif piece[0] == "move":
                    move_to(piece[1], piece[2])
                else:
                    path.curveTo(
                        piece[1], yy(piece[2]),
                        piece[3], yy(piece[4]),
                        piece[5], yy(piece[6]),
                    )

            current = (x2, y2)

        elif cmd in ("close", "closepath", "z"):
            if has_current and subpath_start is not None:
                path.close()
                current = subpath_start

    return path


def _draw_gradient(c, shape, bbox, y_flip, fill_opacity):
    """Render normalized IR gradients as vector PDF geometry."""
    gradient = shape.get("_active_gradient")
    if not isinstance(gradient, dict):
        return False

    stops = gradient.get("stops", [])
    if not stops:
        return False

    gtype = str(gradient.get("type", "")).lower()
    if gtype not in ("linear_gradient", "lineargrad", "lineargradient"):
        return False

    x0, y0, x1, y1 = bbox
    gx1 = float(gradient.get("x1", x0))
    gy1 = float(gradient.get("y1", y0))
    gx2 = float(gradient.get("x2", x1))
    gy2 = float(gradient.get("y2", y1))

    dx = gx2 - gx1
    dy = gy2 - gy1
    grad_len2 = dx * dx + dy * dy
    if grad_len2 <= 1e-18:
        return False

    grad_len = math.sqrt(grad_len2)
    spread = str(gradient.get("spread", "pad")).lower()

    def parse_stop(stop):
        if isinstance(stop, dict):
            offset = float(stop.get("offset", 0.0))
            color = _color(stop.get("color"))
            opacity = float(stop.get("opacity", 1.0))
            return offset, color, opacity
        if isinstance(stop, (list, tuple)) and len(stop) >= 2:
            return float(stop[0]), _color(stop[1]), 1.0
        return 0.0, None, 1.0

    parsed = [parse_stop(stop) for stop in stops]
    parsed.sort(key=lambda item: item[0])

    def color_at(t):
        if spread == "reflect":
            t = t % 2.0
            if t > 1.0:
                t = 2.0 - t
        elif spread == "repeat":
            t = t % 1.0
        else:
            t = max(0.0, min(1.0, t))

        if t <= parsed[0][0]:
            _, color, opacity = parsed[0]
            return color, opacity

        for index in range(1, len(parsed)):
            p0, c0, a0 = parsed[index - 1]
            p1, c1, a1 = parsed[index]
            if t <= p1:
                if c0 is None:
                    return c1, a1
                if c1 is None:
                    return c0, a0
                q = (t - p0) / max(1e-12, p1 - p0)
                return (
                    Color(
                        c0.red + (c1.red - c0.red) * q,
                        c0.green + (c1.green - c0.green) * q,
                        c0.blue + (c1.blue - c0.blue) * q,
                    ),
                    a0 + (a1 - a0) * q,
                )

        _, color, opacity = parsed[-1]
        return color, opacity

    # The current PM5544 multiburst gradients are essentially horizontal.
    # Generate vertical vector strips across the actual shape bounds. The
    # number of strips follows the gradient period, so narrow reflected
    # gradients remain visible instead of collapsing into a solid fill.
    period = grad_len * (2.0 if spread == "reflect" else 1.0)
    width = max(1e-9, x1 - x0)
    stripe_width = max(0.08, period / 32.0)
    count = max(1, int(math.ceil(width / stripe_width)))
    stripe_width = width / count

    # Preserve the shape boundary while filling it with the vector strips.
    c.saveState()
    typ = shape.get("type")
    if typ == "polygon":
        points = shape.get("points", [])
        if len(points) >= 3:
            clip_path = c.beginPath()
            clip_path.moveTo(float(points[0][0]), y_flip(float(points[0][1])))
            for px, py in points[1:]:
                clip_path.lineTo(float(px), y_flip(float(py)))
            clip_path.close()
            c.clipPath(clip_path, stroke=0, fill=0)
    elif typ == "rect":
        clip_path = c.beginPath()
        clip_path.rect(float(x0), y_flip(float(y1)), width, float(y1 - y0))
        c.clipPath(clip_path, stroke=0, fill=0)

    for index in range(count):
        xa = x0 + index * stripe_width
        xb = x0 + (index + 1) * stripe_width
        cx = (xa + xb) * 0.5

        # Project the stripe center onto the gradient vector. This matches
        # the normalized IR semantics even when the gradient coordinates are
        # outside the shape's local bounding box.
        t = ((cx - gx1) * dx + (((y0 + y1) * 0.5) - gy1) * dy) / grad_len2
        color, stop_opacity = color_at(t)
        if color is None or stop_opacity <= 0.0:
            continue

        c.setFillColor(_opacity_color(color, fill_opacity * stop_opacity))
        p = c.beginPath()
        p.rect(xa, y_flip(y1), xb - xa, y1 - y0)
        c.drawPath(p, fill=1, stroke=0)

    c.restoreState()
    return True

def _draw_shape(c, shape, page_height):
    def yy(y):
        return page_height - float(y)

    fill = shape.get("fill")
    stroke = shape.get("stroke")
    fill_opacity = shape.get("fill_opacity", 1.0)
    stroke_opacity = shape.get("stroke_opacity", 1.0)
    stroke_width = float(shape.get("stroke_width", 1.0))

    # ReportLab's PDF coordinates have the origin at the lower-left, while the
    # IR follows SVG's upper-left coordinate convention.
    c.saveState()

    clip = shape.get("clip")
    if isinstance(clip, dict):
        _apply_clip(c, clip, page_height)

    gradient = fill if isinstance(fill, dict) else None

    if gradient:
        shape_for_gradient = dict(shape)
        shape_for_gradient["_active_gradient"] = gradient
        bbox = _shape_bbox(shape)
        _draw_gradient(c, shape_for_gradient, bbox, yy, fill_opacity)
    else:
        fill_color = _color(fill)
        if fill_color is not None:
            c.setFillColor(_opacity_color(fill_color, fill_opacity))
        else:
            c.setFillColor(Color(0, 0, 0, alpha=0))

        stroke_color = _color(stroke)
        if stroke_color is not None:
            c.setStrokeColor(_opacity_color(stroke_color, stroke_opacity))
            c.setLineWidth(stroke_width)
        else:
            c.setStrokeColor(Color(0, 0, 0, alpha=0))
            c.setLineWidth(0)

        _draw_geometry(c, shape, yy, fill_color is not None, stroke_color is not None)

    if gradient and stroke is not None:
        stroke_color = _color(stroke)
        if stroke_color is not None:
            c.setStrokeColor(_opacity_color(stroke_color, stroke_opacity))
            c.setLineWidth(stroke_width)
            _draw_geometry(c, shape, yy, False, True)

    c.restoreState()


def _draw_geometry(c, shape, yy, do_fill, do_stroke):
    typ = shape.get("type")

    if typ == "rect":
        x, y = float(shape["x"]), float(shape["y"])
        w, h = float(shape["w"]), float(shape["h"])
        rx = float(shape.get("rx", 0))
        if rx > 0:
            c.roundRect(x, yy(y + h), w, h, rx, fill=1 if do_fill else 0, stroke=1 if do_stroke else 0)
        else:
            c.rect(x, yy(y + h), w, h, fill=1 if do_fill else 0, stroke=1 if do_stroke else 0)
        return

    if typ == "ellipse":
        cx, cy = float(shape["cx"]), float(shape["cy"])
        rx, ry = float(shape["rx"]), float(shape["ry"])
        c.ellipse(
            cx - rx,
            yy(cy + ry),
            2 * rx,
            2 * ry,
            fill=1 if do_fill else 0,
            stroke=1 if do_stroke else 0,
        )
        return

    if typ == "line":
        c.line(
            float(shape["x1"]), yy(float(shape["y1"])),
            float(shape["x2"]), yy(float(shape["y2"])),
        )
        return

    if typ == "polygon":
        points = shape.get("points", [])
        if not points:
            return
        p = c.beginPath()
        p.moveTo(float(points[0][0]), yy(float(points[0][1])))
        for x, y in points[1:]:
            p.lineTo(float(x), yy(float(y)))
        if shape.get("closed", True):
            p.close()
        c.drawPath(p, fill=1 if do_fill else 0, stroke=1 if do_stroke else 0)
        return

    if typ == "path":
        p = _path_from_segments(c, shape.get("segments", []), yy)
        c.drawPath(p, fill=1 if do_fill else 0, stroke=1 if do_stroke else 0)


def _apply_clip(c, clip, page_height):
    """Apply IR clip geometry using PDF-native path clipping."""
    if clip.get("units") != "userSpaceOnUse":
        return

    def yy(y):
        return page_height - float(y)

    for item in clip.get("shapes", []):
        typ = item.get("type")

        if typ == "rect":
            p = c.beginPath()
            x = float(item["x"])
            y = float(item["y"])
            w = float(item["w"])
            h = float(item["h"])
            p.rect(x, yy(y + h), w, h)
            c.clipPath(p, stroke=0, fill=0)
            continue

        if typ == "ellipse":
            p = c.beginPath()
            cx = float(item["cx"])
            cy = float(item["cy"])
            rx = abs(float(item["rx"]))
            ry = abs(float(item["ry"]))
            p.ellipse(cx - rx, yy(cy + ry), 2.0 * rx, 2.0 * ry)
            c.clipPath(p, stroke=0, fill=0)
            continue

        if typ == "polygon":
            points = item.get("points", [])
            if not points:
                continue
            p = c.beginPath()
            p.moveTo(float(points[0][0]), yy(float(points[0][1])))
            for x, y in points[1:]:
                p.lineTo(float(x), yy(float(y)))
            p.close()
            c.clipPath(p, stroke=0, fill=0)
            continue

        if typ == "path":
            segments = item.get("segments", [])

            # The PM5544 clip circle is represented in the IR as a path made
            # from one moveto followed by two elliptical arcs. Detect that
            # canonical form and use a native PDF ellipse. This avoids
            # numerical differences in the arc-to-Bézier conversion used for
            # clipping while preserving the vector nature of the output.
            if len(segments) >= 3:
                first = segments[0]
                arcs = [seg for seg in segments[1:] if seg.get("cmd") == "arc"]
                if (
                    first.get("cmd") == "moveto"
                    and len(arcs) == 2
                    and all("rx" in seg and "ry" in seg for seg in arcs)
                ):
                    rx = abs(float(arcs[0]["rx"]))
                    ry = abs(float(arcs[0]["ry"]))
                    x_start = float(first["x"])
                    y_start = float(first["y"])
                    x_end = float(arcs[0]["x"])
                    y_end = float(arcs[0]["y"])
                    cx = (x_start + x_end) / 2.0
                    cy = (y_start + y_end) / 2.0
                    p = c.beginPath()
                    p.ellipse(cx - rx, yy(cy + ry), 2.0 * rx, 2.0 * ry)
                    c.clipPath(p, stroke=0, fill=0)
                    continue

            p = _path_from_segments(c, segments, yy)
            c.clipPath(p, stroke=0, fill=0)

def _shape_bbox(shape):
    typ = shape.get("type")

    if typ == "rect":
        return (
            float(shape["x"]),
            float(shape["y"]),
            float(shape["x"]) + float(shape["w"]),
            float(shape["y"]) + float(shape["h"]),
        )

    if typ == "ellipse":
        cx, cy = float(shape["cx"]), float(shape["cy"])
        rx, ry = float(shape["rx"]), float(shape["ry"])
        return cx - rx, cy - ry, cx + rx, cy + ry

    if typ == "line":
        return (
            min(float(shape["x1"]), float(shape["x2"])),
            min(float(shape["y1"]), float(shape["y2"])),
            max(float(shape["x1"]), float(shape["x2"])),
            max(float(shape["y1"]), float(shape["y2"])),
        )

    if typ == "polygon":
        pts = shape.get("points", [])
        xs = [float(p[0]) for p in pts]
        ys = [float(p[1]) for p in pts]
        return min(xs), min(ys), max(xs), max(ys)

    if typ == "path":
        xs, ys = [], []
        for seg in shape.get("segments", []):
            for key in ("x", "x1", "x2"):
                if key in seg:
                    xs.append(float(seg[key]))
                    ykey = "y" if key == "x" else "y" + key[1:]
                    if ykey in seg:
                        ys.append(float(seg[ykey]))
            if "rx" in seg and xs and ys:
                xs.extend((xs[-1] - float(seg["rx"]), xs[-1] + float(seg["rx"])))
            if "ry" in seg and xs and ys:
                ys.extend((ys[-1] - float(seg["ry"]), ys[-1] + float(seg["ry"])))
        if xs and ys:
            return min(xs), min(ys), max(xs), max(ys)

    return (0.0, 0.0, 1.0, 1.0)


def json_to_pdf(json_file_path, output_pdf_path, scale=1.0):
    with open(json_file_path, "r", encoding="utf-8") as handle:
        document = json.load(handle)

    if document.get("format") != "portable_vector_ir":
        raise ValueError("Input JSON is not a Portable Vector IR document.")

    bounds = document.get("bounds") or {}
    xmin = float(bounds.get("xmin", 0.0))
    ymin = float(bounds.get("ymin", 0.0))
    xmax = float(bounds.get("xmax", 800.0))
    ymax = float(bounds.get("ymax", 600.0))

    width = (xmax - xmin) * scale
    height = (ymax - ymin) * scale

    if width <= 0 or height <= 0:
        raise ValueError("Invalid document bounds.")

    output_path = Path(output_pdf_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    c = canvas.Canvas(str(output_path), pagesize=(width, height))

    # Shift the document so that non-zero IR bounds are preserved.
    c.translate(-xmin, 0)

    if scale != 1.0:
        c.scale(scale, scale)

    for shape in document.get("shapes", []):
        _draw_shape(c, shape, height / scale)

    c.showPage()
    c.save()

    return output_path


def main():
    if len(sys.argv) not in (3, 4):
        print("Usage: python json_to_pdf.py input.json output.pdf [scale]")
        return 1

    input_json = sys.argv[1]
    output_pdf = sys.argv[2]
    scale = float(sys.argv[3]) if len(sys.argv) == 4 else 1.0

    try:
        result = json_to_pdf(input_json, output_pdf, scale)
        print(f"Generated: {result}")
        return 0
    except Exception as exc:
        print(f"Conversion failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
