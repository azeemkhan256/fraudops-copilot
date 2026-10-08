"""Builds the public project website from docs/website/index.html.

- Injects benchmark/results/summary.json into the page (Model comparison section).
- Writes docs/website/site/ (index.html with a full HTML skeleton + img/), ready for GitHub Pages / Netlify.

  python docs/website/build_site.py [--video URL] [--team "A, B, C"]
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

ap = argparse.ArgumentParser()
ap.add_argument("--video", help="link to the final demonstration video")
ap.add_argument("--team", help="team member names")
a = ap.parse_args()

page = (HERE / "index.html").read_text(encoding="utf-8")
summary = ROOT / "benchmark" / "results" / "summary.json"
if summary.exists():
    data = json.loads(summary.read_text(encoding="utf-8"))
    page = re.sub(r'(<script id="bench-data" type="application/json">).*?(</script>)',
                  lambda m: m.group(1) + json.dumps(data).replace("</", "<\\/") + m.group(2), page, flags=re.S)
    print(f"Injected benchmark results for {len(data['models'])} models")
if a.video:
    url = html.escape(a.video, quote=True)
    page = re.sub(r'(<div class="pending" id="video-box">).*?(</div>)',
                  lambda m: f'{m.group(1)}<p><a href="{url}">Watch the final demonstration video</a></p>{m.group(2)}', page, flags=re.S)
if a.team:
    page = page.replace("[team member names]", html.escape(a.team))
(HERE / "index.html").write_text(page, encoding="utf-8")

out = HERE / "site"
if out.exists():
    shutil.rmtree(out)
shutil.copytree(HERE / "img", out / "img")
(out / "index.html").write_text(
    '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"></head><body>\n'
    + page + "\n</body></html>\n", encoding="utf-8")
print(f"Static site written to {out}")
