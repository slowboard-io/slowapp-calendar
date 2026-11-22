"""ui/page.html with the bridge (ui/bridge.js) inlined: one self-contained file at the [ui] entry.

    python3 tools/build_ui.py ui/page.html ui/bridge.js /ui/index.html
"""
import re
import sys

page, bridge, out = sys.argv[1:4]
js = open(bridge).read()
# An inline module: no exports, just the declarations.
js = re.sub(r"^export (const|class|function|async function) ", r"\1 ", js, flags=re.M)
html = open(page).read()
assert html.count("/*@bridge*/") == 1, "page needs one /*@bridge*/ marker"
with open(out, "w") as f:
    f.write(html.replace("/*@bridge*/", js))
