#!/usr/bin/env python3
"""Build index.html: embed the static SVG resources (shell.html, untouched)
and inject the main rendering logic (app.src.js) as an XOR-91 obfuscated,
base64-encoded blob that self-decodes at load time.
"""
import base64
import os

HERE = os.path.dirname(os.path.abspath(__file__))
XOR_KEY = 91


def obfuscate(js_source: str) -> str:
    data = js_source.encode("utf-8")
    xored = bytes(b ^ XOR_KEY for b in data)
    return base64.b64encode(xored).decode("ascii")


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

    out = shell.replace("/*__OBFUSCATED_BOOTSTRAP__*/", bootstrap)
    out_path = os.path.join(HERE, "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"wrote {out_path} ({len(encoded)} bytes of obfuscated payload)")


if __name__ == "__main__":
    build()
