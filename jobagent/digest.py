"""Builds the daily email: an HTML digest plus a plain-text fallback."""
import html
from collections import Counter
from datetime import datetime

CSS = """
body{margin:0;padding:0;background:#f4f4f2;font-family:-apple-system,BlinkMacSystemFont,
'Segoe UI',Helvetica,Arial,sans-serif;color:#1c1c1a;}
.wrap{max-width:640px;margin:0 auto;padding:24px 16px 40px;}
.head{padding-bottom:16px;border-bottom:2px solid #1c1c1a;margin-bottom:24px;}
.head h1{margin:0 0 4px;font-size:20px;letter-spacing:-.01em;}
.head p{margin:0;font-size:13px;color:#6b6b66;}
.section{margin:28px 0 10px;font-size:12px;font-weight:700;letter-spacing:.08em;
text-transform:uppercase;color:#6b6b66;}
.card{background:#fff;border:1px solid #e2e2dd;border-radius:10px;padding:16px 18px;margin-bottom:12px;}
.card.strong{border-left:4px solid #1f6f4a;}
.card.look{border-left:4px solid #c9a227;}
.role{font-size:16px;font-weight:700;margin:0 0 2px;line-height:1.3;}
.role a{color:#1c1c1a;text-decoration:none;}
.co{font-size:14px;color:#3d3d39;margin:0 0 8px;}
.meta{font-size:12px;color:#6b6b66;margin:0 0 10px;}
.meta span{margin-right:10px;white-space:nowrap;}
.why{font-size:14px;line-height:1.5;margin:0 0 12px;color:#26261f;}
.tags{font-size:11px;color:#7a7a72;margin:0 0 12px;line-height:1.6;}
.btn{display:inline-block;background:#1c1c1a;color:#fff !important;text-decoration:none;
font-size:13px;font-weight:600;padding:8px 16px;border-radius:6px;}
.score{float:right;font-size:12px;font-weight:700;color:#1f6f4a;}
.foot{margin-top:32px;padding-top:16px;border-top:1px solid #e2e2dd;font-size:12px;color:#8a8a82;line-height:1.6;}
.row{background:#fff;border:1px solid #e2e2dd;border-radius:8px;padding:10px 14px;
margin-bottom:6px;display:block;text-decoration:none;}
.row .n{display:inline-block;min-width:26px;font-size:11px;font-weight:700;color:#8a8a82;}
.row .t{font-size:14px;font-weight:600;color:#1c1c1a;}
.row .c{font-size:13px;color:#3d3d39;}
.row .m{font-size:11px;color:#8a8a82;margin:2px 0 0 26px;}
.note{font-size:12px;color:#8a8a82;margin:0 0 12px;line-height:1.5;}
.empty{background:#fff;border:1px dashed #d5d5cf;border-radius:10px;padding:24px;
text-align:center;color:#6b6b66;font-size:14px;}
"""


def safe_url(url):
    """Only https links reach her inbox. Job data is third-party text, not trusted input."""
    url = (url or "").strip()
    return url if url.lower().startswith("https://") else ""


def _card(job):
    bits = []
    if job.location:
        bits.append(html.escape(job.location))
    if job.comp_text:
        bits.append(html.escape(job.comp_text))
    if job.posted:
        bits.append("posted " + html.escape(job.posted))
    bits.append(job.source)
    why = job.why or "; ".join(job.reasons[:3])
    url = html.escape(safe_url(job.url))
    link = f'<a class="btn" href="{url}">View posting</a>' if url else ""
    title = html.escape(job.title)
    role = f'<a href="{url}">{title}</a>' if url else title
    return f"""
    <div class="card {job.tier}">
      <span class="score">{job.score}</span>
      <p class="role">{role}</p>
      <p class="co">{html.escape(job.company)}</p>
      <p class="meta">{''.join(f'<span>{b}</span>' for b in bits)}</p>
      <p class="why">{html.escape(why)}</p>
      <p class="tags">{html.escape('  |  '.join(job.reasons[:4]))}</p>
      {link}
    </div>"""


def _row(job):
    """Compact line for the ranked tail. No rationale, just the facts and the catch."""
    url = html.escape(safe_url(job.url))
    meta = [b for b in (job.location, job.comp_text) if b]
    catch = next((r for r in job.reasons
                  if r.startswith(("outside her range", "comp below floor", "posted", "remote but"))), "")
    if catch:
        meta.append(catch)
    inner = (f'<span class="n">{job.score}</span>'
             f'<span class="t">{html.escape(job.title)}</span> '
             f'<span class="c">{html.escape(job.company)}</span>'
             f'<div class="m">{html.escape("  |  ".join(meta))}</div>')
    return f'<a class="row" href="{url}">{inner}</a>' if url else f'<div class="row">{inner}</div>'


def cut_summary(hard_drops):
    """One honest line about what was filtered out, so the silence is explained."""
    LABELS = {"junior": ("too junior", "too junior"),
              "staffing": ("staffing firm", "staffing firms"),
              "function": ("outside your field", "outside your field")}
    buckets = Counter()
    for job in hard_drops:
        r = (job.reasons or [""])[0]
        if r.startswith("too junior"):
            buckets["junior"] += 1
        elif r.startswith(("staffing firm", "company on skip")):
            buckets["staffing"] += 1
        else:
            buckets["function"] += 1
    if not buckets:
        return ""
    parts = ", ".join(f"{n} {LABELS[k][0 if n == 1 else 1]}" for k, n in buckets.most_common())
    return f"Hid {sum(buckets.values())}: {parts}."


def build_html(strong, look, rest, stats, profile):
    today = datetime.now().strftime("%A, %B %d").replace(" 0", " ")
    total = len(strong) + len(look)
    everything = total + len(rest)
    parts = [f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><style>{CSS}</style></head>
<body><div class="wrap">
<div class="head">
  <h1>{total} new role{'' if total == 1 else 's'} worth your time</h1>
  <p>{today} &middot; {everything} new in your field, {stats['fetched']} postings scanned across {stats['sources']} sources</p>
</div>"""]

    if not total:
        parts.append('<div class="empty">Nothing new cleared the bar today.<br>'
                     'The scan ran clean, the market was just quiet.</div>')
    if strong:
        parts.append('<p class="section">Strong match</p>')
        parts.extend(_card(j) for j in strong)
    if look:
        parts.append('<p class="section">Worth a look</p>')
        parts.extend(_card(j) for j in look)
    if rest:
        parts.append('<p class="section">Everything else, ranked</p>')
        parts.append('<p class="note">In your field, but something holds each one back. '
                     'The reason is on the line.</p>')
        parts.extend(_row(j) for j in rest)

    parts.append(f"""
<div class="foot">
  Filtered for {html.escape(profile['headline'])} roles, remote US or Boston metro,
  base at or above ${profile['comp_floor']:,}.<br>
  {html.escape(stats.get('cut_line', ''))}<br>
  Duplicates and anything sent before are suppressed automatically.
</div></div></body></html>""")
    return "".join(parts)


def build_text(strong, look, rest=()):
    lines = []
    for label, group in (("STRONG MATCH", strong), ("WORTH A LOOK", look)):
        if not group:
            continue
        lines.append(label)
        lines.append("=" * len(label))
        for j in group:
            lines.append(f"{j.title} - {j.company} ({j.score})")
            meta = " | ".join(x for x in (j.location, j.comp_text, j.posted) if x)
            if meta:
                lines.append("  " + meta)
            if j.why:
                lines.append("  " + j.why)
            lines.append("  " + j.url)
            lines.append("")
    return "\n".join(lines) or "Nothing new cleared the bar today."


def subject(strong, look, rest=()):
    total = len(strong) + len(look) + len(rest)
    if not total:
        return "Job scan: nothing new today"
    if strong:
        top = strong[0]
        extra = total - 1
        tail = f" (+{extra} more)" if extra else ""
        return f"{top.title} at {top.company}{tail}"
    if look:
        return f"{len(look)} worth a look, {len(rest)} more in your field"
    return f"{total} new role{'' if total == 1 else 's'} in your field"
