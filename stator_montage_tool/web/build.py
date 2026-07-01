#!/usr/bin/env python3
"""Build index.html: embed the static SVG resources (shell.html + real process
icons under assets/, untouched) and inject the main rendering logic
(app.src.js) as an XOR-91 obfuscated, base64-encoded blob that self-decodes
at load time.
"""
import base64
import glob
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(HERE, "assets")
XOR_KEY = 91

_SVG_OPEN_RE = re.compile(r"(<svg\b[^>]*)(>)", re.IGNORECASE)


def obfuscate(js_source: str) -> str:
    data = js_source.encode("utf-8")
    xored = bytes(b ^ XOR_KEY for b in data)
    return base64.b64encode(xored).decode("ascii")


def _with_viewbox(svg_text, width, height):
    if "viewBox" in svg_text:
        return svg_text
    return _SVG_OPEN_RE.sub(rf'\1 viewBox="0 0 {width} {height}"\2', svg_text, count=1)


def _namespace_ids(svg_text, prefix):
    """Each source asset was exported standalone and reuses generic ids like
    clip0/img0. Embedding several together in one document makes those ids
    collide (url(#clip0) then resolves to the wrong element and the icon
    renders blank), so prefix every id and its references to keep them
    unique per icon."""
    ids = set(re.findall(r'\bid="([^"]+)"', svg_text))
    for old_id in ids:
        new_id = f"{prefix}-{old_id}"
        svg_text = re.sub(rf'\bid="{re.escape(old_id)}"', f'id="{new_id}"', svg_text)
        svg_text = re.sub(rf'url\(#{re.escape(old_id)}\)', f'url(#{new_id})', svg_text)
        svg_text = re.sub(rf'((?:xlink:)?href)="#{re.escape(old_id)}"', rf'\1="#{new_id}"', svg_text)
    return svg_text


def load_real_icon_templates():
    """Read every assets/*.svg (verbatim, real artwork), ensure a viewBox so
    it scales inside a grid cell, de-duplicate internal ids, and wrap each
    as a hidden <template>."""
    templates = []
    for path in sorted(glob.glob(os.path.join(ASSETS_DIR, "*.svg"))):
        name = os.path.splitext(os.path.basename(path))[0]
        with open(path, encoding="utf-8") as f:
            svg_text = f.read().strip()
        m = re.search(r'<svg\b[^>]*\bwidth="([\d.]+)"[^>]*\bheight="([\d.]+)"', svg_text)
        width, height = (m.group(1), m.group(2)) if m else ("100", "100")
        svg_text = _with_viewbox(svg_text, width, height)
        svg_text = _namespace_ids(svg_text, name)
        templates.append(f'  <template id="icon-{name}">\n    {svg_text}\n  </template>')
    return "\n".join(templates)


def build():
    with open(os.path.join(HERE, "app.src.js"), encoding="utf-8") as f:
        js_source = f.read()
    with open(os.path.join(HERE, "shell.html"), encoding="utf-8") as f:
        shell = f.read()

    encoded = obfuscate(js_source)
    bootstrap = (
        "(function(){"
        "var e=\"" + encoded + "\";"
        "var d=atob(e);"
        "var b=new Uint8Array(d.length);"
        "for(var i=0;i<d.length;i++){b[i]=d.charCodeAt(i)^" + str(XOR_KEY) + ";}"
        "var o=new TextDecoder('utf-8').decode(b);"
        "(new Function(o))();"
        "})();"
    )

    out = shell.replace("<!--__REAL_ICON_TEMPLATES__-->", load_real_icon_templates())
    out = out.replace("/*__OBFUSCATED_BOOTSTRAP__*/", bootstrap)
    out_path = os.path.join(HERE, "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"wrote {out_path} ({len(encoded)} bytes of obfuscated payload)")


if __name__ == "__main__":
    build()
