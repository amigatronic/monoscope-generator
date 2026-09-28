# -*- coding: utf-8 -*-
"""
SVG geometry and style normalization core.

This module contains no graphics-library dependency. It parses SVG geometry,
styles, gradients, and clip paths into a portable intermediate representation.

The key rule is that SVG transforms are resolved during normalization. Target
renderers do not need to implement SVG transform semantics.
"""
import math
import re
import xml.etree.ElementTree as ET

IDENTITY_TRANSFORM = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
_SODIPODI_NS = "{http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd}"


def _tag(elem):
    tag = elem.tag
    return tag.split("}", 1)[-1] if "}" in tag else tag


def compose_transform(outer, inner):
    """Return outer * inner for SVG affine matrices (a,b,c,d,e,f)."""
    a1, b1, c1, d1, e1, f1 = outer
    a2, b2, c2, d2, e2, f2 = inner
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def invert_transform(transform):
    a, b, c, d, e, f = transform
    det = a * d - b * c
    if abs(det) < 1e-15:
        return IDENTITY_TRANSFORM
    ia, ib, ic, id_ = d / det, -b / det, -c / det, a / det
    ie = -(ia * e + ic * f)
    iff = -(ib * e + id_ * f)
    return ia, ib, ic, id_, ie, iff


def apply_transform(transform, x, y):
    a, b, c, d, e, f = transform
    return a * x + c * y + e, b * x + d * y + f


def parse_transform(transform_str):
    """Parse the SVG transform list into one affine matrix."""
    result = IDENTITY_TRANSFORM
    if not transform_str:
        return result

    pattern = r"([A-Za-z]+)\s*\(([^)]*)\)"
    for name, args_str in re.findall(pattern, transform_str):
        args = [
            float(value)
            for value in re.split(r"[\s,]+", args_str.strip())
            if value.strip()
        ]
        name = name.lower()
        local = IDENTITY_TRANSFORM

        if name == "matrix" and len(args) == 6:
            local = tuple(args)
        elif name == "translate":
            tx = args[0] if args else 0.0
            ty = args[1] if len(args) > 1 else 0.0
            local = (1.0, 0.0, 0.0, 1.0, tx, ty)
        elif name == "scale":
            sx = args[0] if args else 1.0
            sy = args[1] if len(args) > 1 else sx
            local = (sx, 0.0, 0.0, sy, 0.0, 0.0)
        elif name == "rotate":
            angle = math.radians(args[0]) if args else 0.0
            cos_a = math.cos(angle)
            sin_a = math.sin(angle)
            rotation = (cos_a, sin_a, -sin_a, cos_a, 0.0, 0.0)
            if len(args) >= 3:
                cx, cy = args[1], args[2]
                local = compose_transform(
                    (1.0, 0.0, 0.0, 1.0, cx, cy),
                    compose_transform(
                        rotation,
                        (1.0, 0.0, 0.0, 1.0, -cx, -cy),
                    ),
                )
            else:
                local = rotation
        elif name == "skewx" and args:
            local = (1.0, 0.0, math.tan(math.radians(args[0])), 1.0, 0.0, 0.0)
        elif name == "skewy" and args:
            local = (1.0, math.tan(math.radians(args[0])), 0.0, 1.0, 0.0, 0.0)

        result = compose_transform(result, local)

    return result


def _tokenize_path(value):
    return re.findall(
        r"[A-Za-z]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?",
        value,
    )


def parse_path_segments(d_string):
    """Parse an SVG path into absolute M/L/C/Q/A/Z segments."""
    segments = []
    if not d_string:
        return segments

    tokens = _tokenize_path(d_string)
    if not tokens:
        return segments

    i = 0
    cmd = ""
    cx = cy = 0.0
    sx = sy = 0.0
    last_cubic_ctrl = None
    last_quad_ctrl = None

    def reset_controls(active):
        nonlocal last_cubic_ctrl, last_quad_ctrl
        if active not in ("C", "c", "S", "s"):
            last_cubic_ctrl = None
        if active not in ("Q", "q", "T", "t"):
            last_quad_ctrl = None

    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
        elif cmd == "M":
            cmd = "L"
        elif cmd == "m":
            cmd = "l"
        else:
            break

        reset_controls(cmd)

        if cmd in ("Z", "z"):
            segments.append({"cmd": "close"})
            cx, cy = sx, sy
            continue

        try:
            if cmd == "M":
                cx, cy = float(tokens[i]), float(tokens[i + 1])
                sx, sy = cx, cy
                segments.append({"cmd": "moveto", "x": cx, "y": cy})
                i += 2
            elif cmd == "m":
                cx += float(tokens[i])
                cy += float(tokens[i + 1])
                sx, sy = cx, cy
                segments.append({"cmd": "moveto", "x": cx, "y": cy})
                i += 2
            elif cmd == "L":
                cx, cy = float(tokens[i]), float(tokens[i + 1])
                segments.append({"cmd": "lineto", "x": cx, "y": cy})
                i += 2
            elif cmd == "l":
                cx += float(tokens[i])
                cy += float(tokens[i + 1])
                segments.append({"cmd": "lineto", "x": cx, "y": cy})
                i += 2
            elif cmd == "H":
                cx = float(tokens[i])
                segments.append({"cmd": "lineto", "x": cx, "y": cy})
                i += 1
            elif cmd == "h":
                cx += float(tokens[i])
                segments.append({"cmd": "lineto", "x": cx, "y": cy})
                i += 1
            elif cmd == "V":
                cy = float(tokens[i])
                segments.append({"cmd": "lineto", "x": cx, "y": cy})
                i += 1
            elif cmd == "v":
                cy += float(tokens[i])
                segments.append({"cmd": "lineto", "x": cx, "y": cy})
                i += 1
            elif cmd == "C":
                c1 = (float(tokens[i]), float(tokens[i + 1]))
                c2 = (float(tokens[i + 2]), float(tokens[i + 3]))
                end = (float(tokens[i + 4]), float(tokens[i + 5]))
                segments.append({
                    "cmd": "cubic", "x1": c1[0], "y1": c1[1],
                    "x2": c2[0], "y2": c2[1], "x": end[0], "y": end[1],
                })
                cx, cy = end
                last_cubic_ctrl = c2
                i += 6
            elif cmd == "c":
                c1 = (cx + float(tokens[i]), cy + float(tokens[i + 1]))
                c2 = (cx + float(tokens[i + 2]), cy + float(tokens[i + 3]))
                end = (cx + float(tokens[i + 4]), cy + float(tokens[i + 5]))
                segments.append({
                    "cmd": "cubic", "x1": c1[0], "y1": c1[1],
                    "x2": c2[0], "y2": c2[1], "x": end[0], "y": end[1],
                })
                cx, cy = end
                last_cubic_ctrl = c2
                i += 6
            elif cmd in ("S", "s"):
                if last_cubic_ctrl is not None:
                    c1 = (2.0 * cx - last_cubic_ctrl[0], 2.0 * cy - last_cubic_ctrl[1])
                else:
                    c1 = (cx, cy)
                if cmd == "S":
                    c2 = (float(tokens[i]), float(tokens[i + 1]))
                    end = (float(tokens[i + 2]), float(tokens[i + 3]))
                else:
                    c2 = (cx + float(tokens[i]), cy + float(tokens[i + 1]))
                    end = (cx + float(tokens[i + 2]), cy + float(tokens[i + 3]))
                segments.append({
                    "cmd": "cubic", "x1": c1[0], "y1": c1[1],
                    "x2": c2[0], "y2": c2[1], "x": end[0], "y": end[1],
                })
                cx, cy = end
                last_cubic_ctrl = c2
                i += 4
            elif cmd == "Q":
                c = (float(tokens[i]), float(tokens[i + 1]))
                end = (float(tokens[i + 2]), float(tokens[i + 3]))
                segments.append({
                    "cmd": "quad", "x1": c[0], "y1": c[1],
                    "x": end[0], "y": end[1],
                })
                cx, cy = end
                last_quad_ctrl = c
                i += 4
            elif cmd == "q":
                c = (cx + float(tokens[i]), cy + float(tokens[i + 1]))
                end = (cx + float(tokens[i + 2]), cy + float(tokens[i + 3]))
                segments.append({
                    "cmd": "quad", "x1": c[0], "y1": c[1],
                    "x": end[0], "y": end[1],
                })
                cx, cy = end
                last_quad_ctrl = c
                i += 4
            elif cmd in ("T", "t"):
                if last_quad_ctrl is not None:
                    c = (2.0 * cx - last_quad_ctrl[0], 2.0 * cy - last_quad_ctrl[1])
                else:
                    c = (cx, cy)
                if cmd == "T":
                    end = (float(tokens[i]), float(tokens[i + 1]))
                else:
                    end = (cx + float(tokens[i]), cy + float(tokens[i + 1]))
                segments.append({
                    "cmd": "quad", "x1": c[0], "y1": c[1],
                    "x": end[0], "y": end[1],
                })
                cx, cy = end
                last_quad_ctrl = c
                i += 2
            elif cmd in ("A", "a"):
                rx = abs(float(tokens[i]))
                ry = abs(float(tokens[i + 1]))
                phi = float(tokens[i + 2])
                large_arc = bool(int(float(tokens[i + 3])))
                sweep = bool(int(float(tokens[i + 4])))
                if cmd == "A":
                    end = (float(tokens[i + 5]), float(tokens[i + 6]))
                else:
                    end = (cx + float(tokens[i + 5]), cy + float(tokens[i + 6]))
                segments.append({
                    "cmd": "arc", "x1": cx, "y1": cy,
                    "rx": rx, "ry": ry, "phi": phi,
                    "large_arc": large_arc, "sweep": sweep,
                    "x": end[0], "y": end[1],
                })
                cx, cy = end
                i += 7
            else:
                raise ValueError(f"Unsupported SVG path command: {cmd}")
        except (IndexError, ValueError):
            break

    return segments


def parse_points(value):
    values = [
        float(item)
        for item in re.split(r"[\s,]+", value.strip())
        if item.strip()
    ]
    return [[values[i], values[i + 1]] for i in range(0, len(values) - 1, 2)]


def transform_point_list(points, transform):
    return [list(apply_transform(transform, x, y)) for x, y in points]


def transform_path_segments(segments, transform):
    """Apply an affine transform to path geometry while preserving curves."""
    result = []
    a, b, c, d, _, _ = transform
    # For an arbitrary affine transform an SVG elliptical arc cannot always be
    # represented by the same rx/ry/phi tuple. Convert the arc to cubic Beziers
    # only in this exceptional case. Lines and Beziers remain parametric.
    linear_is_rotation_scale = abs(a * c + b * d) < 1e-10
    for segment in segments:
        cmd = segment["cmd"]
        if cmd in ("moveto", "lineto"):
            x, y = apply_transform(transform, segment["x"], segment["y"])
            result.append({"cmd": cmd, "x": x, "y": y})
        elif cmd == "cubic":
            x1, y1 = apply_transform(transform, segment["x1"], segment["y1"])
            x2, y2 = apply_transform(transform, segment["x2"], segment["y2"])
            x, y = apply_transform(transform, segment["x"], segment["y"])
            result.append({"cmd": "cubic", "x1": x1, "y1": y1,
                           "x2": x2, "y2": y2, "x": x, "y": y})
        elif cmd == "quad":
            x1, y1 = apply_transform(transform, segment["x1"], segment["y1"])
            x, y = apply_transform(transform, segment["x"], segment["y"])
            result.append({"cmd": "quad", "x1": x1, "y1": y1, "x": x, "y": y})
        elif cmd == "arc" and linear_is_rotation_scale:
            sx = math.hypot(a, b)
            sy = math.hypot(c, d)
            rotation = math.degrees(math.atan2(b, a))
            x, y = apply_transform(transform, segment["x"], segment["y"])
            x1, y1 = apply_transform(transform, segment["x1"], segment["y1"])
            result.append({
                "cmd": "arc", "x1": x1, "y1": y1,
                "rx": segment["rx"] * sx,
                "ry": segment["ry"] * sy,
                "phi": segment["phi"] + rotation,
                "large_arc": segment["large_arc"],
                "sweep": segment["sweep"], "x": x, "y": y,
            })
        elif cmd == "arc":
            result.extend(_arc_to_cubics(segment, transform))
        elif cmd == "close":
            result.append({"cmd": "close"})
    return result


def _arc_to_cubics(segment, transform):
    """Convert one SVG endpoint-parameterized arc to cubic Beziers."""
    x1, y1 = segment["x1"], segment["y1"]
    x2, y2 = segment["x"], segment["y"]
    rx, ry = segment["rx"], segment["ry"]
    phi = math.radians(segment["phi"] % 360.0)
    large_arc = bool(segment["large_arc"])
    sweep = bool(segment["sweep"])

    if abs(x2 - x1) < 1e-15 and abs(y2 - y1) < 1e-15:
        return []
    if rx <= 1e-15 or ry <= 1e-15:
        ex, ey = apply_transform(transform, x2, y2)
        return [{"cmd": "lineto", "x": ex, "y": ey}]

    cos_phi = math.cos(phi)
    sin_phi = math.sin(phi)
    dx = (x1 - x2) / 2.0
    dy = (y1 - y2) / 2.0
    xp = cos_phi * dx + sin_phi * dy
    yp = -sin_phi * dx + cos_phi * dy

    rx = abs(rx)
    ry = abs(ry)
    lam = (xp * xp) / (rx * rx) + (yp * yp) / (ry * ry)
    if lam > 1.0:
        scale = math.sqrt(lam)
        rx *= scale
        ry *= scale

    sign = -1.0 if large_arc == sweep else 1.0
    numerator = max(0.0, rx * rx * ry * ry - rx * rx * yp * yp - ry * ry * xp * xp)
    denominator = rx * rx * yp * yp + ry * ry * xp * xp
    coef = sign * math.sqrt(numerator / denominator) if denominator > 0 else 0.0
    cxp = coef * (rx * yp / ry)
    cyp = coef * (-ry * xp / rx)

    cx = cos_phi * cxp - sin_phi * cyp + (x1 + x2) / 2.0
    cy = sin_phi * cxp + cos_phi * cyp + (y1 + y2) / 2.0

    def angle(ux, uy, vx, vy):
        dot = ux * vx + uy * vy
        det = ux * vy - uy * vx
        return math.atan2(det, dot)

    ux = (xp - cxp) / rx
    uy = (yp - cyp) / ry
    vx = (-xp - cxp) / rx
    vy = (-yp - cyp) / ry
    theta1 = angle(1.0, 0.0, ux, uy)
    delta = angle(ux, uy, vx, vy)
    if not sweep and delta > 0:
        delta -= 2.0 * math.pi
    elif sweep and delta < 0:
        delta += 2.0 * math.pi

    count = max(1, int(math.ceil(abs(delta) / (math.pi / 2.0))))
    step = delta / count
    result = []

    def ellipse_point(theta):
        ct, st = math.cos(theta), math.sin(theta)
        return (
            cx + rx * cos_phi * ct - ry * sin_phi * st,
            cy + rx * sin_phi * ct + ry * cos_phi * st,
        )

    for index in range(count):
        t0 = theta1 + index * step
        t1 = t0 + step
        alpha = 4.0 / 3.0 * math.tan((t1 - t0) / 4.0)
        p0 = ellipse_point(t0)
        p3 = ellipse_point(t1)
        d0 = (-rx * cos_phi * math.sin(t0) - ry * sin_phi * math.cos(t0),
              -rx * sin_phi * math.sin(t0) + ry * cos_phi * math.cos(t0))
        d1 = (-rx * cos_phi * math.sin(t1) - ry * sin_phi * math.cos(t1),
              -rx * sin_phi * math.sin(t1) + ry * cos_phi * math.cos(t1))
        p1 = (p0[0] + alpha * d0[0], p0[1] + alpha * d0[1])
        p2 = (p3[0] - alpha * d1[0], p3[1] - alpha * d1[1])
        q1 = apply_transform(transform, *p1)
        q2 = apply_transform(transform, *p2)
        q3 = apply_transform(transform, *p3)
        result.append({"cmd": "cubic", "x1": q1[0], "y1": q1[1],
                       "x2": q2[0], "y2": q2[1], "x": q3[0], "y": q3[1]})
    return result


def parse_css_styles(root):
    """Parse simple SVG class rules from embedded CSS."""
    css = {}
    for style_elem in root.iter():
        if _tag(style_elem) != "style" or not style_elem.text:
            continue
        for selector, body in re.findall(r"([^{}]+)\{([^{}]+)\}", style_elem.text):
            props = _parse_style_declarations(body)
            for selector_part in selector.split(","):
                selector_part = selector_part.strip()
                if selector_part.startswith("."):
                    css["class:" + selector_part[1:]] = props
                elif selector_part.startswith("#"):
                    css["id:" + selector_part[1:]] = props
                elif re.match(r"^[A-Za-z_][\w.-]*$", selector_part):
                    css["tag:" + selector_part.lower()] = props
    return css


def _parse_style_declarations(style_text):
    props = {}
    for item in style_text.split(";"):
        if ":" in item:
            key, value = item.split(":", 1)
            props[key.strip().lower()] = value.strip()
    return props


def _parse_number(value, default=0.0):
    if value is None:
        return default
    value = str(value).strip()
    if value.endswith("%"):
        return float(value[:-1]) / 100.0
    try:
        return float(value)
    except ValueError:
        return default


def _parse_color_with_opacity(color, opacity=1.0):
    return {"color": color, "opacity": float(opacity)}


def _resolve_href(value):
    if value and value.startswith("#"):
        return value[1:]
    return value


def parse_gradients(root):
    """Parse linear and radial gradients, including inherited attributes."""
    raw = {}
    for grad in root.iter():
        kind = _tag(grad)
        if kind not in ("linearGradient", "radialGradient"):
            continue
        grad_id = grad.get("id")
        if not grad_id:
            continue
        stops = []
        for stop in grad:
            if _tag(stop) != "stop":
                continue
            declarations = _parse_style_declarations(stop.get("style", ""))
            offset = _parse_number(stop.get("offset", declarations.get("offset", "0")))
            color = stop.get("stop-color", declarations.get("stop-color", "black"))
            opacity = _parse_number(stop.get("stop-opacity", declarations.get("stop-opacity", "1")), 1.0)
            stops.append({"offset": max(0.0, min(1.0, offset)), "color": color, "opacity": opacity})
        raw[grad_id] = {
            "kind": "linear" if kind == "linearGradient" else "radial",
            "href": _resolve_href(grad.get(_XLINK_HREF) or grad.get("href")),
            "attrs": dict(grad.attrib),
            "stops": stops,
        }

    resolved = {}

    def resolve(grad_id, stack=None):
        if grad_id in resolved:
            return resolved[grad_id]
        stack = set() if stack is None else set(stack)
        if grad_id in stack or grad_id not in raw:
            return None
        stack.add(grad_id)
        source = raw[grad_id]
        parent = resolve(source["href"], stack) if source["href"] else None
        result = {
            "kind": source["kind"] or (parent or {}).get("kind", "linear"),
            "attrs": dict(parent.get("attrs", {}) if parent else {}),
            "stops": list(parent.get("stops", []) if parent else []),
        }
        result["attrs"].update(source["attrs"])
        if source["stops"]:
            result["stops"] = source["stops"]
        resolved[grad_id] = result
        return result

    gradient_defs = {}
    gradient_colors = {}
    for grad_id in raw:
        grad = resolve(grad_id)
        if not grad or not grad["stops"]:
            continue
        gradient_colors[grad_id] = grad["stops"][0]["color"]
        attrs = grad["attrs"]
        spread = attrs.get("spreadMethod", "pad")
        gt = parse_transform(attrs.get("gradientTransform", ""))
        if grad["kind"] == "linear":
            gradient_defs[grad_id] = {
                "type": "linear_gradient",
                "units": attrs.get("gradientUnits", "objectBoundingBox"),
                "x1": attrs.get("x1", "0%"),
                "y1": attrs.get("y1", "0%"),
                "x2": attrs.get("x2", "100%"),
                "y2": attrs.get("y2", "0%"),
                "transform": list(gt),
                "spread": spread,
                "stops": grad["stops"],
            }
        else:
            gradient_defs[grad_id] = {
                "type": "radial_gradient",
                "units": attrs.get("gradientUnits", "objectBoundingBox"),
                "cx": attrs.get("cx", "50%"),
                "cy": attrs.get("cy", "50%"),
                "r": attrs.get("r", "50%"),
                "fx": attrs.get("fx", attrs.get("cx", "50%")),
                "fy": attrs.get("fy", attrs.get("cy", "50%")),
                "transform": list(gt),
                "spread": spread,
                "stops": grad["stops"],
            }
    return gradient_colors, gradient_defs


def _resolve_url_ref(value, gradient_colors, gradient_defs):
    if not isinstance(value, str):
        return value
    match = re.fullmatch(r"url\(\s*#([\w-]+)\s*\)", value.strip())
    if not match:
        return value
    grad_id = match.group(1)
    return gradient_defs.get(grad_id, gradient_colors.get(grad_id, "black"))


def resolve_style(element, css_dict, gradient_colors, gradient_defs, parent_style=None):
    """Resolve inherited SVG presentation properties and CSS rules."""
    inherited = {
        "fill": "black",
        "stroke": "none",
        "stroke-width": "1",
        "fill-opacity": "1",
        "stroke-opacity": "1",
        "opacity": "1",
        "display": "inline",
        "visibility": "visible",
    }
    if parent_style:
        inherited.update(parent_style)
        inherited.pop("clip-path", None)

    style = dict(inherited)
    selectors = []
    if element.get("id"):
        selectors.append("id:" + element.get("id"))
    for cls in element.get("class", "").split():
        selectors.append("class:" + cls)
    selectors.append("tag:" + _tag(element).lower())
    for selector in reversed(selectors):
        if selector in css_dict:
            style.update(css_dict[selector])

    style.update(_parse_style_declarations(element.get("style", "")))

    for key in (
        "fill", "stroke", "stroke-width", "fill-opacity", "stroke-opacity",
        "opacity", "display", "visibility", "clip-path",
    ):
        if key in element.attrib:
            style[key] = element.attrib[key]

    style["fill"] = _resolve_url_ref(style.get("fill", "black"), gradient_colors, gradient_defs)
    style["stroke"] = _resolve_url_ref(style.get("stroke", "none"), gradient_colors, gradient_defs)
    return style


def _shape_bbox(shape):
    kind = shape["type"]
    if kind == "rect":
        return shape["x"], shape["y"], shape["x"] + shape["w"], shape["y"] + shape["h"]
    if kind == "ellipse":
        return (
            shape["cx"] - shape["rx"], shape["cy"] - shape["ry"],
            shape["cx"] + shape["rx"], shape["cy"] + shape["ry"],
        )
    if kind == "line":
        return (
            min(shape["x1"], shape["x2"]), min(shape["y1"], shape["y2"]),
            max(shape["x1"], shape["x2"]), max(shape["y1"], shape["y2"]),
        )
    if kind == "polygon":
        points = shape.get("points", [])
        if points:
            xs, ys = zip(*points)
            return min(xs), min(ys), max(xs), max(ys)
    if kind == "path":
        points = []
        for seg in shape.get("segments", []):
            for key in ("x", "x1", "x2"):
                if key in seg:
                    ykey = "y" + key[1:] if key != "x" else "y"
                    if ykey in seg:
                        points.append((seg[key], seg[ykey]))
        if points:
            xs, ys = zip(*points)
            return min(xs), min(ys), max(xs), max(ys)
    return 0.0, 0.0, 0.0, 0.0


def normalize_gradient(paint, bbox, shape_transform=IDENTITY_TRANSFORM):
    """Resolve gradient units into document coordinates and remove its transform."""
    if not isinstance(paint, dict) or "type" not in paint:
        return paint

    x0, y0, x1, y1 = bbox
    width = x1 - x0
    height = y1 - y0
    gradient = dict(paint)
    gt = tuple(gradient.pop("transform", IDENTITY_TRANSFORM))
    units = gradient.pop("units", "objectBoundingBox")

    def coord(value, start, size):
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value).strip()
        if text.endswith("%"):
            return start + float(text[:-1]) / 100.0 * size
        return float(text)

    if gradient["type"] == "linear_gradient":
        if units == "objectBoundingBox":
            gx1 = coord(gradient.get("x1", "0%"), x0, width)
            gy1 = coord(gradient.get("y1", "0%"), y0, height)
            gx2 = coord(gradient.get("x2", "100%"), x0, width)
            gy2 = coord(gradient.get("y2", "0%"), y0, height)
        else:
            gx1 = coord(gradient.get("x1", "0"), 0.0, 1.0)
            gy1 = coord(gradient.get("y1", "0"), 0.0, 1.0)
            gx2 = coord(gradient.get("x2", "1"), 0.0, 1.0)
            gy2 = coord(gradient.get("y2", "0"), 0.0, 1.0)
        gx1, gy1 = apply_transform(gt, gx1, gy1)
        gx2, gy2 = apply_transform(gt, gx2, gy2)
        if units != "objectBoundingBox":
            gx1, gy1 = apply_transform(shape_transform, gx1, gy1)
            gx2, gy2 = apply_transform(shape_transform, gx2, gy2)
        gradient.update({"x1": gx1, "y1": gy1, "x2": gx2, "y2": gy2})
        return gradient

    # Radial gradients retain a document-space ellipse. This representation is
    # portable and does not require SVG transform semantics in the target.
    if units == "objectBoundingBox":
        cx = coord(gradient.get("cx", "50%"), x0, width)
        cy = coord(gradient.get("cy", "50%"), y0, height)
        r = float(str(gradient.get("r", "50%")).rstrip("%")) / 100.0 * max(abs(width), abs(height))
        fx = coord(gradient.get("fx", gradient.get("cx", "50%")), x0, width)
        fy = coord(gradient.get("fy", gradient.get("cy", "50%")), y0, height)
    else:
        cx, cy = float(gradient.get("cx", 0)), float(gradient.get("cy", 0))
        r = float(gradient.get("r", 0))
        fx, fy = float(gradient.get("fx", cx)), float(gradient.get("fy", cy))
    cx, cy = apply_transform(gt, cx, cy)
    fx, fy = apply_transform(gt, fx, fy)
    gradient.update({"cx": cx, "cy": cy, "r": r, "fx": fx, "fy": fy})
    gradient["type"] = "radial_gradient"
    return gradient


def parse_clip_paths(root):
    """Parse clip paths into ordinary parametric shape records."""
    clips = {}
    for clip in root.iter():
        if _tag(clip) != "clipPath":
            continue
        clip_id = clip.get("id")
        if not clip_id:
            continue
        clip_units = clip.get("clipPathUnits", "userSpaceOnUse")
        records = []
        for child in clip:
            tag = _tag(child)
            transform = parse_transform(child.get("transform", ""))
            if tag == "rect":
                shape = {
                    "type": "rect",
                    "x": _parse_number(child.get("x")),
                    "y": _parse_number(child.get("y")),
                    "w": _parse_number(child.get("width")),
                    "h": _parse_number(child.get("height")),
                }
                if child.get("rx") is not None:
                    shape["rx"] = _parse_number(child.get("rx"))
                records.append(normalize_shape_geometry(shape, transform))
            elif tag == "circle":
                shape = {
                    "type": "ellipse",
                    "cx": _parse_number(child.get("cx")),
                    "cy": _parse_number(child.get("cy")),
                    "rx": _parse_number(child.get("r")),
                    "ry": _parse_number(child.get("r")),
                }
                records.append(normalize_shape_geometry(shape, transform))
            elif tag == "ellipse":
                shape = {
                    "type": "ellipse",
                    "cx": _parse_number(child.get("cx")),
                    "cy": _parse_number(child.get("cy")),
                    "rx": _parse_number(child.get("rx")),
                    "ry": _parse_number(child.get("ry")),
                }
                records.append(normalize_shape_geometry(shape, transform))
            elif tag == "line":
                shape = {
                    "type": "line",
                    "x1": _parse_number(child.get("x1")),
                    "y1": _parse_number(child.get("y1")),
                    "x2": _parse_number(child.get("x2")),
                    "y2": _parse_number(child.get("y2")),
                }
                records.append(normalize_shape_geometry(shape, transform))
            elif tag in ("polyline", "polygon"):
                shape = {
                    "type": "polygon",
                    "points": parse_points(child.get("points", "")),
                    "closed": tag == "polygon",
                }
                records.append(normalize_shape_geometry(shape, transform))
            elif tag == "path":
                shape = {
                    "type": "path",
                    "segments": parse_path_segments(child.get("d", "")),
                }
                records.append(normalize_shape_geometry(shape, transform))
        clips[clip_id] = {"units": clip_units, "shapes": records}
    return clips


def _ellipse_to_path(shape, transform):
    """Represent an ellipse as four cubic Bezier segments."""
    cx, cy = shape["cx"], shape["cy"]
    rx, ry = shape["rx"], shape["ry"]
    k = 0.5522847498307936
    source = [
        (cx + rx, cy),
        (cx + rx, cy + k * ry),
        (cx + k * rx, cy + ry),
        (cx, cy + ry),
        (cx - k * rx, cy + ry),
        (cx - rx, cy + k * ry),
        (cx - rx, cy),
        (cx - rx, cy - k * ry),
        (cx - k * rx, cy - ry),
        (cx, cy - ry),
        (cx + k * rx, cy - ry),
        (cx + rx, cy - k * ry),
    ]
    p0 = apply_transform(transform, *source[0])
    segments = [{"cmd": "moveto", "x": p0[0], "y": p0[1]}]
    for index in range(0, 12, 3):
        c1 = apply_transform(transform, *source[index + 1])
        c2 = apply_transform(transform, *source[index + 2])
        end = apply_transform(transform, *source[(index + 3) % 12])
        segments.append({
            "cmd": "cubic", "x1": c1[0], "y1": c1[1],
            "x2": c2[0], "y2": c2[1], "x": end[0], "y": end[1],
        })
    segments.append({"cmd": "close"})
    return {"type": "path", "segments": segments}


def normalize_shape_geometry(shape, transform=IDENTITY_TRANSFORM):
    """Apply an affine transform and return geometry with no transform field."""
    if transform == IDENTITY_TRANSFORM:
        return shape
    kind = shape["type"]
    if kind == "line":
        x1, y1 = apply_transform(transform, shape["x1"], shape["y1"])
        x2, y2 = apply_transform(transform, shape["x2"], shape["y2"])
        return {"type": "line", "x1": x1, "y1": y1, "x2": x2, "y2": y2}
    if kind == "polygon":
        return {
            "type": "polygon",
            "points": transform_point_list(shape.get("points", []), transform),
            "closed": shape.get("closed", True),
        }
    if kind == "path":
        return {"type": "path", "segments": transform_path_segments(shape.get("segments", []), transform)}
    if kind == "ellipse":
        return _ellipse_to_path(shape, transform)
    if kind == "rect":
        x, y, w, h = shape["x"], shape["y"], shape["w"], shape["h"]
        points = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        if "rx" in shape and shape["rx"] > 0:
            # Rounded rectangles are represented as cubic paths so rotation,
            # skew, and non-uniform scaling are all handled geometrically.
            rx = min(shape["rx"], w / 2.0)
            ry = min(shape.get("ry", rx), h / 2.0)
            k = 0.5522847498307936
            local = [
                (x + rx, y), (x + w - rx, y),
                (x + w - rx + k * rx, y), (x + w, y + ry - k * ry),
                (x + w, y + ry), (x + w, y + h - ry),
                (x + w, y + h - ry + k * ry), (x + w - rx + k * rx, y + h),
                (x + w - rx, y + h), (x + rx, y + h),
                (x + rx - k * rx, y + h), (x, y + h - ry + k * ry),
                (x, y + h - ry), (x, y + ry),
                (x, y + ry - k * ry), (x + rx - k * rx, y),
            ]
            p = apply_transform(transform, *local[0])
            segments = [{"cmd": "moveto", "x": p[0], "y": p[1]}]
            p = apply_transform(transform, *local[1])
            segments.append({"cmd": "lineto", "x": p[0], "y": p[1]})
            for start in (2, 6, 10, 14):
                c1 = apply_transform(transform, *local[start])
                c2 = apply_transform(transform, *local[start + 1])
                end = apply_transform(transform, *local[start + 2])
                segments.append({"cmd": "cubic", "x1": c1[0], "y1": c1[1],
                                 "x2": c2[0], "y2": c2[1], "x": end[0], "y": end[1]})
                if start != 14:
                    next_end = apply_transform(transform, *local[start + 3])
                    segments.append({"cmd": "lineto", "x": next_end[0], "y": next_end[1]})
            segments.append({"cmd": "close"})
            return {"type": "path", "segments": segments}
        return {
            "type": "polygon",
            "points": transform_point_list(points, transform),
            "closed": True,
        }
    return shape
