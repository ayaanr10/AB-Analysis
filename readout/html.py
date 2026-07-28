"""Generated HTML scorecard (SPEC.md §7.9, resolving §12 Q3).

Q3 asked whether the scorecard should be Tableau or generated HTML. Generated HTML, for
three reasons, with the cost of the choice stated rather than hidden:

1. **It cannot disagree with the analysis.** This file renders the same `Readout` object
   the memo and the CLI render. A Tableau workbook reads an extract, so the moment the
   pipeline changes, the workbook is stale and nothing tells you. Every number here comes
   from the same objects that produced the memo, through the same gate.
2. **The blocking rule extends to it for free.** SPEC.md §7.3 requires suppression in
   *every* output surface. Here that is one `if readout.blocked` branch. In Tableau it
   would be a calculated field someone could unhide, and the gate would become advisory.
3. **It is reproducible by anyone.** `python -m readout.cli html --config ...` needs no
   Tableau licence and no manual republish step.

**What this choice costs.** The argument for Tableau was a portfolio one: it would add a
second artifact to an existing Tableau Public profile. That is a real loss and it is not
recovered by anything here. The technical argument won because a scorecard that can silently
disagree with the analysis behind it is worse than no scorecard.

"Parameterized so either experiment loads without rebuilding" (§7.9) holds in the form the
choice permits: one generator, any config, no edits. Nothing below names a dataset, a
variant, a metric or a horizon.
"""

from __future__ import annotations

import html
from datetime import date

from .gate import Status

_STATUS_CLASS = {
    Status.PASS: "pass",
    Status.WARN: "warn",
    Status.BLOCK: "block",
    Status.NOT_APPLICABLE: "na",
}

_CSS = """
:root {
  --bg:#fbfbfa; --fg:#1a1a19; --muted:#6b6b68; --line:#e2e2de; --card:#fff;
  --pass:#2d7a4f; --warn:#a86a12; --block:#b3261e; --na:#8a8a86; --accent:#1f4e79;
}
@media (prefers-color-scheme: dark) {
  :root { --bg:#161614; --fg:#eeeeea; --muted:#a3a39e; --line:#33332f; --card:#1f1f1c;
          --pass:#6ec18f; --warn:#e0a94a; --block:#f2857c; --na:#8a8a86; --accent:#8ab6e0; }
}
* { box-sizing:border-box; }
body { margin:0; padding:2rem 1.25rem 4rem; background:var(--bg); color:var(--fg);
  font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,sans-serif; }
main { max-width:60rem; margin:0 auto; }
h1 { font-size:1.5rem; margin:0 0 .25rem; letter-spacing:-.01em; }
h2 { font-size:.78rem; text-transform:uppercase; letter-spacing:.09em; color:var(--muted);
  margin:2.25rem 0 .7rem; font-weight:600; }
.sub { color:var(--muted); margin:0 0 1.75rem; }
.card { background:var(--card); border:1px solid var(--line); border-radius:9px;
  padding:1.1rem 1.25rem; margin-bottom:.7rem; }
.verdict { border-left:5px solid var(--accent); }
.verdict.blocked { border-left-color:var(--block); }
.verdict h1 { color:var(--accent); }
.verdict.blocked h1 { color:var(--block); }
table { width:100%; border-collapse:collapse; font-variant-numeric:tabular-nums; }
th,td { text-align:right; padding:.5rem .6rem; border-bottom:1px solid var(--line); }
th:first-child, td:first-child { text-align:left; }
th { font-size:.72rem; text-transform:uppercase; letter-spacing:.06em; color:var(--muted);
  font-weight:600; }
tbody tr:last-child td { border-bottom:none; }
.scroll { overflow-x:auto; }
.tag { display:inline-block; font-size:.68rem; font-weight:700; letter-spacing:.05em;
  padding:.16rem .45rem; border-radius:4px; border:1px solid currentColor; }
.pass{color:var(--pass)} .warn{color:var(--warn)} .block{color:var(--block)} .na{color:var(--na)}
.diag { display:grid; grid-template-columns:auto 1fr; gap:.5rem .9rem; align-items:baseline; }
.diag .txt { color:var(--muted); font-size:.9rem; }
.banner { border-left:4px solid var(--warn); background:color-mix(in srgb,var(--warn) 8%,transparent);
  padding:.8rem 1rem; border-radius:0 7px 7px 0; margin-bottom:.7rem; font-size:.92rem; }
.sig { color:var(--pass); font-weight:600; }
.nosig { color:var(--muted); }
.controls { display:flex; gap:.4rem; flex-wrap:wrap; margin-bottom:.7rem; }
.controls button { font:inherit; font-size:.85rem; padding:.35rem .8rem; cursor:pointer;
  background:var(--card); color:var(--fg); border:1px solid var(--line); border-radius:6px; }
.controls button[aria-pressed="true"] { border-color:var(--accent); color:var(--accent);
  font-weight:600; }
footer { color:var(--muted); font-size:.82rem; margin-top:2.5rem; padding-top:1rem;
  border-top:1px solid var(--line); }
code { font-size:.85em; background:color-mix(in srgb,var(--fg) 7%,transparent);
  padding:.1rem .3rem; border-radius:3px; }
"""

_JS = """
document.querySelectorAll('[data-horizon-filter]').forEach(function (btn) {
  btn.addEventListener('click', function () {
    var want = btn.getAttribute('data-horizon-filter');
    document.querySelectorAll('[data-horizon-filter]').forEach(function (b) {
      b.setAttribute('aria-pressed', String(b === btn));
    });
    document.querySelectorAll('tr[data-horizon]').forEach(function (row) {
      row.hidden = want !== '*' && row.getAttribute('data-horizon') !== want;
    });
  });
});
"""


def _esc(text) -> str:
    return html.escape(str(text), quote=True)


def _fmt(value: float, is_rate: bool) -> str:
    return f"{value:.4%}" if is_rate else f"{value:,.2f}"


def _diagnostics_card(readout) -> str:
    rows = []
    for r in readout.gate.results:
        cls = _STATUS_CLASS[r.status]
        rows.append(
            f'<span class="tag {cls}">{_esc(r.status.symbol)}</span>'
            f'<span><strong>{_esc(r.label)}</strong><br>'
            f'<span class="txt">{_esc(r.headline)}</span></span>'
        )
    return f'<div class="card"><div class="diag">{"".join(rows)}</div></div>'


def _power_card(readout) -> str:
    body = [
        "<div class='card scroll'><table><thead><tr><th>Horizon</th><th>Baseline</th>"
        "<th>Smallest detectable effect</th><th>Worth acting on</th>"
        "<th>Powered for that?</th></tr></thead><tbody>"
    ]
    for hp in readout.power:
        r = hp.result
        threshold = f"{r.practical_effect:.1%}" if r.practical_effect is not None else "&mdash;"
        answer = {True: '<span class="sig">yes</span>', False: '<span class="block">no</span>',
                  None: "&mdash;"}[r.can_answer_its_own_question]
        body.append(
            f"<tr><td>{_esc(hp.horizon_label)}</td><td>{r.baseline_rate:.2%}</td>"
            f"<td>{r.mde_relative:.2%} rel ({r.mde_absolute:+.3%} abs)</td>"
            f"<td>{threshold}</td><td>{answer}</td></tr>"
        )
    body.append("</tbody></table></div>")
    return "".join(body)


def _metrics_card(readout) -> str:
    results = readout.require_results()
    horizons = []
    for r in results:
        if r.horizon_label not in horizons:
            horizons.append(r.horizon_label)

    controls = ['<div class="controls"><button data-horizon-filter="*" aria-pressed="true">'
                "All horizons</button>"]
    for h in horizons:
        controls.append(
            f'<button data-horizon-filter="{_esc(h)}" aria-pressed="false">{_esc(h)}</button>'
        )
    controls.append("</div>")

    rows = []
    for r in results:
        is_rate = r.metric_type == "binary_rate"
        verdict = ('<span class="sig">yes</span>' if r.is_significant
                   else '<span class="nosig">no</span>')
        role = "" if r.is_primary else ' <span class="txt">(guardrail)</span>'
        ci = ("&mdash;" if r.relative_effect is None
              else f"{r.ci_relative.low:+.2%} to {r.ci_relative.high:+.2%}")
        rows.append(
            f'<tr data-horizon="{_esc(r.horizon_label)}">'
            f"<td>{_esc(r.metric_label)}{role}<br><span class='txt'>{_esc(r.horizon_label)}</span></td>"
            f"<td>{_fmt(r.control_value, is_rate)}</td>"
            f"<td>{_fmt(r.treatment_value, is_rate)}</td>"
            f"<td>{_esc(r.relative_text)}</td>"
            f"<td>{ci}</td>"
            f"<td>{r.p_value:.4f}</td><td>{verdict}</td></tr>"
        )

    warnings = "".join(
        f'<div class="banner"><strong>{_esc(w.label)}.</strong> {_esc(w.detail)}</div>'
        for w in readout.gate.warnings
    )
    return (
        "".join(controls) + warnings
        + '<div class="card scroll"><table><thead><tr><th>Metric</th><th>Control</th>'
        "<th>Treatment</th><th>Effect</th><th>95% CI (relative)</th><th>p</th>"
        "<th>Readable?</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def _segments_card(readout) -> str:
    report = readout.segments
    if report is None:
        return (
            '<div class="card"><strong>Not possible on this dataset.</strong><br>'
            '<span class="txt">No pre-treatment covariates exist, so there is nothing safe to '
            "split on and nothing to adjust with. Declined rather than approximated.</span></div>"
        )

    head = (
        f'<div class="card"><span class="txt">{report.n_tests} segment comparisons across '
        f"{len(report.covariates)} pre-treatment covariates. "
        f"{len(report.nominally_significant)} reached p &lt; 0.05, where noise alone would "
        f"produce about {report.expected_false_positives:.0f}. "
        f"<strong>{len(report.surviving)}</strong> survive Benjamini-Hochberg correction."
        "</span></div>"
    )
    best = report.best_cuped
    if not best:
        return head
    return head + (
        '<div class="card scroll"><table><thead><tr><th>CUPED on ' + _esc(best.covariate)
        + "</th><th>Before</th><th>After</th></tr></thead><tbody>"
        f"<tr><td>Standard error</td><td>{best.se_raw:.6f}</td><td>{best.se_cuped:.6f}</td></tr>"
        f"<tr><td>95% CI width</td><td>{best.ci_width_raw:.6f}</td>"
        f"<td>{best.ci_width_cuped:.6f}</td></tr>"
        f"<tr><td>Effect estimate</td><td>{best.effect_raw:+.6f}</td>"
        f"<td>{best.effect_cuped:+.6f}</td></tr>"
        "</tbody></table>"
        f'<p class="txt">Variance reduction {best.variance_reduction:.2%} against a '
        f"theoretical ceiling of {best.theoretical_reduction:.2%} "
        f"(corr = {best.correlation:+.4f}). {_cuped_shift_note(best)}</p></div>"
    )


def _cuped_shift_note(best) -> str:
    """Describe what happened to the point estimate, based on what actually happened.

    This sentence used to be hardcoded as "the estimate barely moves", which was true on
    the balanced fixture and false on Criteo, where CUPED corrected a covariate imbalance
    worth a quarter of the effect. A generated page must not assert something its own
    numbers contradict.
    """
    if not best.shift_is_material:
        return (
            "The effect estimate barely moves, which is what should happen: the covariate is "
            "balanced across arms, so there is nothing to correct and CUPED only removes "
            "noise that was never informative."
        )
    return (
        f"The effect estimate moved from {best.effect_raw:+.6f} to {best.effect_cuped:+.6f}, "
        f"which is {best.shift_share:.0%} of it. That is a correction, not an error: the arms "
        f"differ slightly on {_esc(best.covariate)}, which predicts the outcome, so part of "
        "the raw effect was never the treatment. The shift equals "
        "&minus;theta &times; the covariate imbalance exactly. The adjusted figure is the "
        "better estimate, and the gap is information about the randomisation rather than a "
        "problem with the adjustment."
    )


def render_html(readout) -> str:
    cfg = readout.config
    blocked = readout.blocked

    if blocked:
        verdict_title = "Readout blocked"
        verdict_body = (
            "A validity check failed, so the two groups being compared are not comparable and "
            "no effect estimate was computed. Blocking checks: "
            + _esc(", ".join(readout.gate.blocked_by)) + "."
        )
    else:
        from .memo import decide
        verdict, reasoning = decide(readout)
        verdict_title = verdict
        verdict_body = _esc(reasoning)

    parts = [
        f'<main><section class="card verdict{" blocked" if blocked else ""}">',
        f"<h1>{_esc(verdict_title)}</h1>",
        f'<p class="sub">{_esc(cfg.description.strip())}</p>',
        f"<p>{verdict_body}</p></section>",
        "<h2>Validity diagnostics</h2>",
        _diagnostics_card(readout),
    ]

    if blocked:
        parts.append(
            '<div class="card"><span class="txt">Effect estimates are suppressed in every '
            "output surface when a diagnostic blocks, including this one. Nothing was computed, "
            "so there is no number here to quote out of context.</span></div>"
        )
    else:
        parts += [
            "<h2>Power &mdash; shown before results</h2>", _power_card(readout),
            "<h2>Results</h2>", _metrics_card(readout),
            "<h2>Segmentation and CUPED</h2>", _segments_card(readout),
        ]

    parts.append(
        f"<footer>Generated by <code>readout.cli html --config {_esc(readout.config_path)}</code> "
        f"on {date.today().isoformat()} from {readout.load_report.assignment_rows:,} assignments "
        f"and {readout.load_report.event_rows:,} events. Every value on this page comes from the "
        "same pipeline that produced the decision memo.</footer></main>"
    )

    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>Scorecard &mdash; {_esc(cfg.name)}</title>"
        f"<style>{_CSS}</style></head><body>"
        + "".join(parts)
        + f"<script>{_JS}</script></body></html>"
    )
