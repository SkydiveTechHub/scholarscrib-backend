"""Allowlist SVG sanitiser for lesson diagrams.

InteractiveDiagram renders ``block.svg`` through ``dangerouslySetInnerHTML``, so
uploaded markup runs in every student's page. This module therefore *rebuilds*
the SVG from allowlisted tokens instead of stripping bad ones out of the source:
anything not named here never reaches the output. Never convert it to a
blocklist.

The tokenizer is ``html.parser`` (linear time, no regex backtracking), and an
unterminated hostile element swallows the rest of the input — it fails closed.
"""

from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser

SVG_ELEMENTS = {
    "svg", "g", "path", "circle", "ellipse", "rect", "line", "polyline",
    "polygon", "text", "tspan", "defs", "marker", "lineargradient",
    "radialgradient", "stop", "title", "desc",
}  # fmt: skip

# Dropped together with everything inside them.
HOSTILE_ELEMENTS = {
    "script", "style", "foreignobject", "use", "image", "iframe", "animate",
    "animatetransform", "animatemotion", "set", "handler", "embed", "object",
    "link", "meta", "audio", "video",
}  # fmt: skip

SVG_ATTRS = {
    "d", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry",
    "width", "height", "points", "transform", "viewbox", "preserveaspectratio",
    "fill", "fill-opacity", "fill-rule", "stroke", "stroke-width",
    "stroke-linecap", "stroke-linejoin", "stroke-dasharray", "opacity",
    "font-size", "font-family", "font-weight", "text-anchor", "dominant-baseline",
    "offset", "stop-color", "stop-opacity", "gradientunits", "marker-end",
    "marker-start", "id", "class", "xmlns",
}  # fmt: skip

# html.parser lower-cases names; SVG is case-sensitive for these.
_TAG_CASE = {"lineargradient": "linearGradient", "radialgradient": "radialGradient"}
_ATTR_CASE = {
    "viewbox": "viewBox",
    "preserveaspectratio": "preserveAspectRatio",
    "gradientunits": "gradientUnits",
}

# `url(#id)` is a legitimate paint reference; any other url()/scheme is not.
_SAFE_URL = re.compile(r"^url\(\s*#[\w\-:.]+\s*\)$")
_FORBIDDEN_VALUE = re.compile(
    r"(javascript|data|vbscript)\s*:|expression\s*\(|url\s*\(", re.I
)


class _Sanitiser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.warnings: list[str] = []
        self.open_stack: list[str] = []
        self.hostile_depth = 0
        self.hostile_name = ""
        self.seen_root = False
        self.root_closed = False

    # -- helpers -----------------------------------------------------------
    def _warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def _attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> str:
        parts: list[str] = []
        for name, value in attrs:
            lname = name.lower()
            value = value or ""
            if lname.startswith("on"):
                self._warn(f'Removed event handler attribute "{name}".')
                continue
            if lname in {"href", "xlink:href"}:
                if not re.fullmatch(r"#[\w\-:.]+", value.strip()):
                    self._warn(f'Removed "{name}" — only #fragment links are allowed.')
                    continue
            elif lname.startswith("aria-"):
                pass
            elif lname not in SVG_ATTRS:
                self._warn(f'Removed attribute "{name}" from <{tag}>.')
                continue
            if _FORBIDDEN_VALUE.search(value) and not _SAFE_URL.match(value.strip()):
                self._warn(f'Removed unsafe value of "{name}".')
                continue
            parts.append(
                f'{_ATTR_CASE.get(lname, lname)}="{escape(value, quote=True)}"'
            )
        return (" " + " ".join(parts)) if parts else ""

    def _accepts(self, tag: str) -> bool:
        """Whether an allowlisted tag may be emitted at this point."""
        if self.root_closed:
            return False
        if not self.seen_root:
            return tag == "svg"
        return True

    # -- parser callbacks --------------------------------------------------
    def handle_starttag(self, tag, attrs, self_closing=False):
        if self.hostile_depth:
            if tag == self.hostile_name:
                self.hostile_depth += 1
            return
        if tag in HOSTILE_ELEMENTS:
            self._warn(f"Removed <{tag}> element.")
            if not self_closing:
                self.hostile_depth = 1
                self.hostile_name = tag
            return
        if tag not in SVG_ELEMENTS or not self._accepts(tag):
            if tag not in SVG_ELEMENTS:
                self._warn(f"Removed unsupported <{tag}> element.")
            return
        self.seen_root = True
        name = _TAG_CASE.get(tag, tag)
        rendered = self._attrs(tag, attrs)
        if self_closing:
            self.out.append(f"<{name}{rendered}/>")
        else:
            self.out.append(f"<{name}{rendered}>")
            self.open_stack.append(name)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs, self_closing=True)

    def handle_endtag(self, tag):
        if self.hostile_depth:
            if tag == self.hostile_name:
                self.hostile_depth -= 1
            return
        name = _TAG_CASE.get(tag, tag)
        if name in self.open_stack:
            while self.open_stack:
                top = self.open_stack.pop()
                self.out.append(f"</{top}>")
                if top == name:
                    break
            if not self.open_stack:
                self.root_closed = True

    def handle_data(self, data):
        if self.hostile_depth or not self.open_stack:
            return
        self.out.append(escape(data, quote=False))

    # Comments, declarations, processing instructions and CDATA are dropped.
    def handle_comment(self, data):
        return

    def handle_decl(self, decl):
        return

    def handle_pi(self, data):
        return

    def unknown_decl(self, data):
        return


def sanitize_svg(source: str) -> tuple[str, list[str]]:
    """Returns (clean_svg, warnings); clean_svg is "" when no <svg> survives."""
    parser = _Sanitiser()
    parser.feed(source)
    parser.close()
    while parser.open_stack:  # close anything left open, so output is balanced
        parser.out.append(f"</{parser.open_stack.pop()}>")
    svg = "".join(parser.out)
    return (svg if svg.startswith("<svg") else ""), parser.warnings
