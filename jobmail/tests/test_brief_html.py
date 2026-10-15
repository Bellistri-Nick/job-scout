"""The styled HTML copy of the weekly brief."""


def test_brief_html_styles_the_markdown_subset():
    from jobmail.brief_html import to_html
    md = (
        "# Pipeline brief, October 14, 2026\n\n*Recommendations by m. Evidence below.*\n\n"
        "## Recommendations\n\n> The pipeline is moving.\n\n**Now**\n\n"
        "- [ ] Prepare for <Toast> & Pindrop. They are close. *(D69, M12)*\n\n"
        "## Evidence\n\n### Open asks (1)\n\n"
        "| ID | Company | Stage | The ask | Urgency | Waiting |\n|---|---|---|---|---|---|\n"
        "| M1 | Acme | screening | Reply | high | 20d |\n"
    )
    h = to_html(md)
    assert "<h1>Pipeline brief, October 14, 2026</h1>" in h
    assert 'class="obs"' in h and 'class="grp g-now"' in h
    assert '<span class="chip">D69</span>' in h and '<span class="chip">M12</span>' in h
    assert "&lt;Toast&gt; &amp; Pindrop" in h            # model text can never inject markup
    assert "<script" not in h.lower()
    assert 'u u-high' in h and 'w-old' in h              # urgency pill and a 20-day wait flagged
    body = h.split("</style>", 1)[1]
    assert "|---" not in body and "**" not in body and "- [ ]" not in body
