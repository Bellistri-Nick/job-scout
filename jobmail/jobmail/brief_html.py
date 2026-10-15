"""Style the weekly brief for email.

`brief.render()` emits a small, fixed Markdown subset: headings, one blockquote, bold and
italic, bullet and checkbox lists, and pipe tables. This turns exactly that subset into HTML
so the email reads as a designed page instead of raw pipes and asterisks. The Markdown stays
the source of truth and the plain-text part of the message.

Styles live in one <style> block with classes. That keeps the message well under Gmail's
~102 KB clipping limit, which per-element inline styles would blow through on a table this
long. Dark mode follows the client: explicit overrides under prefers-color-scheme for
clients that honor it, and Gmail's own auto-inversion elsewhere.
"""
from __future__ import annotations

import html
import re

_CSS = """
:root { color-scheme: light dark; supported-color-schemes: light dark; }
body { margin:0; padding:0; background:#f3f1ec; }
.wrap { max-width:680px; margin:0 auto; padding:24px 16px 40px; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif; color:#1f2328; font-size:15px; line-height:1.55; }
.card { background:#ffffff; border:1px solid #e3dfd6; border-radius:10px; padding:28px 28px 20px; }
h1 { font-size:26px; line-height:1.2; margin:0 0 6px; letter-spacing:-0.01em; color:#14213d; }
.prov { font-size:12.5px; color:#6b6f76; margin:0 0 4px; }
h2 { font-size:12px; text-transform:uppercase; letter-spacing:0.09em; color:#6b6f76; margin:32px 0 12px; padding-bottom:6px; border-bottom:2px solid #e3dfd6; }
h3 { font-size:15px; margin:22px 0 8px; color:#14213d; }
p { margin:0 0 12px; }
.obs { margin:14px 0 8px; padding:12px 16px; background:#f6f4ee; border-left:4px solid #14213d; border-radius:0 6px 6px 0; font-size:15px; }
.grp { display:inline-block; font-size:12px; font-weight:700; letter-spacing:0.06em; text-transform:uppercase; padding:3px 10px; border-radius:999px; margin:18px 0 8px; }
.g-now { background:#fde8e6; color:#a1281c; }
.g-week { background:#fdf0d5; color:#8a5a00; }
.g-consider { background:#e6eef7; color:#2a4d7a; }
.g-other { background:#ececec; color:#444; }
ul.recs { list-style:none; margin:0; padding:0; }
ul.recs li { margin:0 0 10px; padding:10px 12px 10px 38px; position:relative; background:#fbfaf7; border:1px solid #eeeae0; border-radius:8px; }
ul.recs li:before { content:''; position:absolute; left:12px; top:14px; width:14px; height:14px; border:2px solid #9aa0a8; border-radius:3px; }
.cites { display:block; margin-top:5px; }
.chip { display:inline-block; font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:11px; padding:1px 6px; margin:0 3px 2px 0; border-radius:4px; background:#eceff3; color:#4a5568; }
ul.plain { margin:0 0 12px; padding-left:20px; }
ul.plain li { margin:0 0 6px; }
table { border-collapse:collapse; width:100%; margin:6px 0 14px; font-size:13px; }
th { text-align:left; font-size:11px; text-transform:uppercase; letter-spacing:0.06em; color:#6b6f76; padding:6px 8px; border-bottom:2px solid #e3dfd6; }
td { padding:8px; border-bottom:1px solid #eeeae0; vertical-align:top; }
tr.alt td { background:#fbfaf7; }
td.id, td.num { font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:12px; color:#4a5568; white-space:nowrap; }
td.num { text-align:right; }
.u { display:inline-block; font-size:11px; font-weight:600; padding:1px 8px; border-radius:999px; }
.u-high { background:#fde8e6; color:#a1281c; }
.u-medium { background:#fdf0d5; color:#8a5a00; }
.u-low { background:#ececec; color:#555; }
.w-old { color:#a1281c; font-weight:600; }
.muted { color:#6b6f76; font-size:13px; }
code { font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:90%; background:#eceff3; padding:1px 4px; border-radius:3px; }
.foot { margin:18px 4px 0; font-size:12px; color:#8a8f96; text-align:center; }
@media (prefers-color-scheme: dark) {
  body { background:#14161a !important; }
  .wrap { color:#e6e8eb !important; }
  .card { background:#1c1f24 !important; border-color:#2b3037 !important; }
  h1, h3 { color:#f1f3f5 !important; }
  h2 { color:#9aa0a8 !important; border-color:#2b3037 !important; }
  .prov, .muted { color:#9aa0a8 !important; }
  .obs { background:#23272d !important; border-left-color:#7aa2f7 !important; }
  ul.recs li { background:#23272d !important; border-color:#2b3037 !important; }
  ul.recs li:before { border-color:#6b7280 !important; }
  .chip, code { background:#2b3037 !important; color:#b8c0cc !important; }
  th { color:#9aa0a8 !important; border-color:#2b3037 !important; }
  td { border-color:#2b3037 !important; }
  tr.alt td { background:#23272d !important; }
  td.id, td.num { color:#b8c0cc !important; }
  .g-now, .u-high { background:#4a1f1b !important; color:#ffb4aa !important; }
  .g-week, .u-medium { background:#47340a !important; color:#ffd479 !important; }
  .g-consider { background:#1f3350 !important; color:#a9c8f2 !important; }
  .g-other, .u-low { background:#2b3037 !important; color:#c0c6cf !important; }
  .w-old { color:#ffb4aa !important; }
}
""".strip()

_GROUP_CLASS = {"now": "g-now", "this week": "g-week", "worth considering": "g-consider"}
_CITES = re.compile(r"\s*\*\(([A-Z]\d+(?:,\s*[A-Z]\d+)*)\)\*\s*$")


def _inline(text: str) -> str:
    """Escape, then apply bold, italic and code. Order matters: escape first."""
    t = html.escape(text, quote=False)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", t)
    return t


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _table(rows: list[str]) -> str:
    head = _cells(rows[0])
    body = [_cells(r) for r in rows[2:]]  # rows[1] is the |---| rule
    out = ["<table><thead><tr>" + "".join(f"<th>{_inline(h)}</th>" for h in head) + "</tr></thead><tbody>"]
    for i, cells in enumerate(body):
        out.append('<tr class="alt">' if i % 2 else "<tr>")
        for h, c in zip(head, cells):
            key = h.lower()
            if key == "id":
                out.append(f'<td class="id">{_inline(c)}</td>')
            elif key == "urgency":
                lvl = c.strip().lower()
                cls = f"u-{lvl}" if lvl in {"high", "medium", "low"} else "u-low"
                out.append(f'<td><span class="u {cls}">{_inline(c)}</span></td>')
            elif key == "waiting":
                m = re.match(r"(\d+)d$", c.strip())
                old = m and int(m.group(1)) >= 14
                out.append(f'<td class="num{" w-old" if old else ""}">{_inline(c)}</td>')
            elif key == "value":
                out.append(f'<td class="num">{_inline(c)}</td>')
            else:
                out.append(f"<td>{_inline(c)}</td>")
        out.append("</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def _checkbox_item(text: str) -> str:
    cites = ""
    m = _CITES.search(text)
    if m:
        text = text[: m.start()]
        chips = "".join(f'<span class="chip">{html.escape(c.strip())}</span>' for c in m.group(1).split(","))
        cites = f'<span class="cites">{chips}</span>'
    return f"<li>{_inline(text)}{cites}</li>"


def to_html(markdown: str) -> str:
    lines = markdown.splitlines()
    out: list[str] = []
    i, n = 0, len(lines)
    title = "Pipeline brief"
    while i < n:
        line = lines[i]
        s = line.strip()
        if not s:
            i += 1
        elif s.startswith("# "):
            title = s[2:]
            out.append(f"<h1>{_inline(title)}</h1>")
            i += 1
        elif s.startswith("## "):
            out.append(f"<h2>{_inline(s[3:])}</h2>")
            i += 1
        elif s.startswith("### "):
            out.append(f"<h3>{_inline(s[4:])}</h3>")
            i += 1
        elif s.startswith("> "):
            quote = []
            while i < n and lines[i].strip().startswith("> "):
                quote.append(lines[i].strip()[2:])
                i += 1
            out.append(f'<div class="obs">{_inline(" ".join(quote))}</div>')
        elif s.startswith("|"):
            block = []
            while i < n and lines[i].strip().startswith("|"):
                block.append(lines[i])
                i += 1
            out.append(_table(block) if len(block) >= 2 else "")
        elif s.startswith("- [ ] "):
            items = []
            while i < n and lines[i].strip().startswith("- [ ] "):
                items.append(_checkbox_item(lines[i].strip()[6:]))
                i += 1
            out.append('<ul class="recs">' + "".join(items) + "</ul>")
        elif s.startswith("- "):
            items = []
            while i < n and lines[i].strip().startswith("- "):
                items.append(f"<li>{_inline(lines[i].strip()[2:])}</li>")
                i += 1
            out.append('<ul class="plain">' + "".join(items) + "</ul>")
        elif re.fullmatch(r"\*\*[^*]+\*\*", s):
            label = s[2:-2]
            cls = _GROUP_CLASS.get(label.lower(), "g-other")
            out.append(f'<div><span class="grp {cls}">{html.escape(label)}</span></div>')
            i += 1
        elif s.startswith("*") and s.endswith("*") and not s.startswith("**") and i < 4:
            out.append(f'<p class="prov">{_inline(s)}</p>')
            i += 1
        else:
            out.append(f"<p>{_inline(s)}</p>")
            i += 1
    body = "\n".join(o for o in out if o)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light dark"><meta name="supported-color-schemes" content="light dark">'
        f"<title>{html.escape(title)}</title><style>{_CSS}</style></head>"
        f'<body><div class="wrap"><div class="card">{body}</div>'
        '<div class="foot">Sent by jobmail on your Pi. Every number comes from the tracker database.</div>'
        "</div></body></html>"
    )
