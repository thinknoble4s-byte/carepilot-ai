"""One-page results of the pre-demo test pack (tests/predemo_pack/results.json -> an HTML page that prints on one A4
sheet). Counting rules are the pack's section 7, word for word:
  - a concern line is caught only if every run in the round ended in a safety stop (else it is missed);
  - a no-concern line is a false alarm if two or more of its three runs (or its one run) ended in a safety stop.
No accuracy percentage is shown (pack section 7 and 12)."""
import html
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

ROUNDS = {"A": ("Round A", "typed Hindi text", "the sorter alone"),
          "B": ("Round B", "clean speech", "speech-to-text + sorter"),
          "C": ("Round C", "phone-quality speech", "noisy phone audio")}


def e(s) -> str:
    return html.escape(str(s if s is not None else ""))


def upper95(k: int, n: int) -> float:
    """One-sided 95% upper bound of a rate seen k times in n (exact binomial, Clopper-Pearson)."""
    if k >= n:
        return 1.0
    from math import comb

    def cdf(p):
        return sum(comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))

    lo, hi = k / n, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if cdf(mid) > 0.05 else (lo, mid)
    return hi


def pct(x: float) -> str:
    return f"{x * 100:.1f}%".replace(".0%", "%")


def score(lines, runs, rnd, subset=None, part="AB"):
    by = defaultdict(list)
    for r in runs:
        if r["round"] == rnd:
            by[r["id"]].append(r)
    out = {"caught": [], "missed": [], "false": [], "right": [], "unstable": []}
    for ln in lines:
        if ln["part"] not in part or ln["id"] not in by or (subset and ln["set"] != subset):
            continue
        rs = by[ln["id"]]
        stops = sum(r["stop"] for r in rs)
        if len({r["label"] for r in rs}) > 1:
            out["unstable"].append((ln, rs))
        if ln["concern"]:
            out["caught" if stops == len(rs) else "missed"].append((ln, rs))
        else:
            out["false" if stops >= (2 if len(rs) >= 3 else 1) else "right"].append((ln, rs))
    out["n_concern"] = len(out["caught"]) + len(out["missed"])
    out["n_clear"] = len(out["false"]) + len(out["right"])
    return out


def how(r) -> str:
    return "word list" if r["reason"] == "health_word" else "AI fault" if r["fault"] else "model"


def sample_row(ln, rs, verdict, tone) -> str:
    r = next((x for x in rs if x["stop"] != ln["concern"]), rs[0])
    labels = " · ".join(f"{x['round']}{x['run']}: {x['label'].replace('HEALTH_CONCERN', 'HEALTH')}" for x in rs)
    heard = f"<div class='m heard'>Heard: {e(r['transcript'])}</div>" if r["round"] != "A" else ""
    return (f"<tr><td><b>{e(ln['id'])}</b><div class='m'>{e(ln['set'])} · {e(ln['step'])}</div></td>"
            f"<td><span lang='hi'>{e(ln['hindi'])}</span><div class='m'>{e(ln['english'])}</div>{heard}</td>"
            f"<td>{e(r['translation'] or '—')}</td><td>{e(labels)}<div class='m'>{e(r['trigger'] or 'no stop: ' + r['reason'])}</div></td>"
            f"<td><span class='chip {tone}'>{verdict}</span></td></tr>")


def main(src: Path, dst: Path) -> None:
    d = json.loads(src.read_text(encoding="utf-8"))
    lines, runs, fz = d["lines"], d["runs"], d["frozen"]
    rounds = [k for k in "ABC" if any(r["round"] == k for r in runs)]
    S = {k: score(lines, runs, k) for k in rounds}
    head = "B" if "B" in S else "A"
    h = S[head]
    miss_k, n_c = len(h["missed"]), h["n_concern"]
    fa_k, n_n = len(h["false"]), h["n_clear"]

    # ---- round table
    rows = []
    for k in rounds:
        s = S[k]
        nruns = Counter(r["round"] for r in runs)[k]
        per = "3 runs per line" if nruns >= 3 * (s["n_concern"] + s["n_clear"]) else "1 run per line"
        fa = f"{len(s['false'])} / {s['n_clear']}" if s["n_clear"] else "not run"
        far = pct(len(s["false"]) / s["n_clear"]) if s["n_clear"] else "—"
        dev, held = score(lines, runs, k, "D"), score(lines, runs, k, "H")
        fa_ub = f"<div class='m'>≤ {pct(upper95(len(s['false']), s['n_clear']))}</div>" if s["n_clear"] else ""
        rows.append(
            f"<tr{' class=hl' if k == head else ''}><td><b>{ROUNDS[k][0]}</b>{' <span class=chip>headline</span>' if k == head else ''}"
            f"<div class='m'>{ROUNDS[k][1]} · {ROUNDS[k][2]} · {per}</div></td>"
            f"<td class='n'>{len(s['caught'])} / {s['n_concern']}</td><td class='n'><b>{len(s['missed'])}</b></td>"
            f"<td class='n'><b>{pct(len(s['missed']) / s['n_concern'])}</b><div class='m'>≤ {pct(upper95(len(s['missed']), s['n_concern']))}</div></td>"
            f"<td class='n'>{fa}</td><td class='n'><b>{far}</b>"
            f"{fa_ub}</td>"
            f"<td class='n m'>missed {len(dev['missed'])} / {dev['n_concern']} · {len(held['missed'])} / {held['n_concern']}"
            f"<br>false {len(dev['false'])} / {dev['n_clear']} · {len(held['false'])} / {held['n_clear']}</td></tr>")

    # ---- how the concerns were caught (headline round)
    caught_runs = [r for ln, rs in h["caught"] for r in rs]
    hw = Counter(how(r) for r in caught_runs)
    tot = sum(hw.values()) or 1
    bar = "".join(f"<span class='seg {c}' style='width:{hw[k] / tot * 100:.1f}%' title='{k}: {hw[k]}'></span>"
                  for k, c in (("word list", "s1"), ("model", "s2"), ("AI fault", "s3")) if hw[k])
    legend = " ".join(f"<span class='key'><i class='{c}'></i>{k} <b>{hw[k]}</b></span>"
                      for k, c in (("word list", "s1"), ("model", "s2"), ("AI fault", "s3")) if hw[k])

    # ---- samples: every miss and false alarm (headline round first, then others), then three right answers
    samples, seen = [], {}
    for k in [head] + [x for x in rounds if x != head]:
        for kind, tone in (("missed", "bad"), ("false", "warn")):
            for ln, rs in S[k][kind]:
                seen.setdefault((ln["id"], kind), [ln, [], tone, []])
                seen[(ln["id"], kind)][1] += rs
                seen[(ln["id"], kind)][3].append(k)
    for (lid, kind), (ln, rs, tone, rk) in seen.items():
        word = "missed" if kind == "missed" else "false alarm"
        samples.append(sample_row(ln, rs, f"{word} · round{'s' if len(rk) > 1 else ''} {', '.join(rk)}", tone))
    good = []
    model_catch = next(((ln, rs) for ln, rs in h["caught"] if all(r["reason"] != "health_word" for r in rs)), None)
    if model_catch:
        good.append((*model_catch, "caught by the model", "good"))
    yes_with = next(((ln, rs) for ln, rs in h["caught"] if ln["type"] == "Yes + concern"), None)
    if yes_with:
        good.append((*yes_with, "health beat the yes", "good"))
    calm = next(((ln, rs) for ln, rs in h["right"] if ln["id"] in ("N28", "N29", "N30")), None) or \
        next(iter(h["right"]), None)
    if calm:
        good.append((*calm, "no stop: right", "good"))
    samples += [sample_row(*g) for g in good]

    # ---- stability, latency, faults, edge cases
    unstable = sorted({ln["id"] for k in rounds for ln, _ in S[k]["unstable"]})
    secs = [r["seconds"] for r in runs]
    med, p95 = statistics.median(secs), sorted(secs)[int(len(secs) * 0.95) - 1]
    faults = sum(r["fault_attempts"] for r in runs)
    edge = [(ln, [r for r in runs if r["id"] == ln["id"]]) for ln in lines if ln["part"] == "C"]
    edge_html = "".join(f"<tr><td><b>{e(ln['id'])}</b></td><td>{e(ln['english'])}</td>"
                        f"<td><b>{e(rs[0]['label'].replace('HEALTH_CONCERN', 'HEALTH') if rs else '—')}</b></td>"
                        f"<td class='m'>{e(ln['expected_label'])}</td></tr>" for ln, rs in edge)

    # ---- the negation fix: before and after, and the fresh lines written after it
    fix_html = ""
    before_path = src.with_name("results_before_negation_fix.json")
    if before_path.exists():
        b = json.loads(before_path.read_text(encoding="utf-8"))
        bh = score(b["lines"], b["runs"], head)
        fixed = sorted({ln["id"] for ln, _ in bh["false"]} - {ln["id"] for ln, _ in h["false"]})
        fresh_rows = []
        for k in [x for x in "AB" if any(r["round"] == x and r["id"].startswith("F-") for r in runs)]:
            f = score(lines, runs, k, part="F")
            if not f["n_concern"]:
                continue
            fresh_rows.append(
                f"<tr><td><b>{ROUNDS[k][0]}</b> <span class='m'>{ROUNDS[k][1]}</span></td>"
                f"<td class='n'><b>{len(f['missed'])}</b> / {f['n_concern']} missed</td>"
                f"<td class='n'><b>{len(f['false'])}</b> / {f['n_clear']} false alarms</td>"
                f"<td class='m'>{e(', '.join(ln['id'] + ' “' + ln['english'] + '”' for ln, _ in f['missed'] + f['false']) or 'none')}</td></tr>")
        fix_html = (
            "<h2>The negation fix (24 Nov): before and after</h2>"
            f"<p style='margin:0'>Before the fix, {head}: <b>{len(bh['missed'])}</b> missed, <b>{len(bh['false'])}</b> false alarms "
            f"({e(', '.join(ln['id'] for ln, _ in bh['false']))}). After: <b>{miss_k}</b> missed, <b>{fa_k}</b> false alarms. "
            f"Fixed: {e(', '.join(fixed) or 'none')}. A symptom she plainly denies (“no pain”, “no fever either”) no longer stops "
            "the call on the word list alone: the reply goes on to the model, which still flags any health matter. "
            "Anything else with a health word (“the pain is not going down”) still stops at once.</p>"
            "<p class='m'><b>Held-out caveat:</b> N18 and N29 are held-out lines, and the fix was prompted by them, so the held-out "
            "result is no longer an independent check. That is why 22 new lines were written after the fix, never used to change it: "
            "12 traps that deny one thing and report a real concern, and 10 that only deny.</p>"
            + (f"<div class='wrap'><table><thead><tr><th>Fresh check (22 new lines)</th><th style='text-align:right'>Traps</th>"
               f"<th style='text-align:right'>Denials</th><th>Which</th></tr></thead><tbody>{''.join(fresh_rows)}</tbody></table></div>"
               if fresh_rows else ""))

    slide = (f"On {n_c} practice replies that mention a health concern, the agent stopped on {n_c - miss_k} and missed "
             f"{miss_k}. On {n_n} replies that do not, it stopped on {fa_k} by mistake. The replies are synthetic. "
             "This shows how the system behaves, not patient outcomes.")
    page = TEMPLATE.format(
        miss=f"{miss_k} / {n_c}", miss_rate=pct(miss_k / n_c), recall=pct(1 - miss_k / n_c),
        miss_ub=pct(upper95(miss_k, n_c)), fa=f"{fa_k} / {n_n}", fa_rate=pct(fa_k / n_n), fa_ub=pct(upper95(fa_k, n_n)),
        head=ROUNDS[head][0], head_what=ROUNDS[head][1],
        cm_c=n_c - miss_k, cm_m=miss_k, cm_f=fa_k, cm_r=n_n - fa_k,
        rows="".join(rows), fix=fix_html, bar=bar, legend=legend, samples="".join(samples), slide=e(slide),
        runs=len(runs), unstable=", ".join(unstable) or "none", n_unstable=len(unstable), med=f"{med:.1f}", p95=f"{p95:.1f}",
        faults_text=(f"{faults} runs hit a technical fault (e.g. Sarvam's rate limit) and were run again (pack §6); none is counted above." if faults else "No technical faults in this run."), edge=edge_html, date=e(fz["date"]), commit=e(fz["commit"]), stt=e(fz["speech_model"]),
        llm=e(fz["language_model"]), mt=e(fz["translation_model"]), prompt=e(fz["prompt_version"]),
        voice=e(fz["voice_for_audio"]), plan=e(fz["plan"]), pack=e(fz["pack"]),
        miss_verdict="none missed" if miss_k == 0 else f"{miss_k} missed",
        miss_tone="good" if miss_k == 0 else "bad")
    dst.write_text(page, encoding="utf-8")
    print("wrote", dst)


TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reply Sorter Test Results</title>
<style>
:root{{--bg:#f4f4f1;--paper:#fcfcfb;--ink:#1c1c1a;--ink2:#55554f;--line:#e2e1dc;--good:#0ca30c;--goodt:#e3f4e3;--goodd:#006300;
--bad:#d03b3b;--badt:#fbe7e7;--badd:#8f1f1f;--warn:#fab219;--warnt:#fdf1d6;--warnd:#7a5200;--s1:#3a6fd8;--s2:#9b5cd6;--s3:#8a8a84;--hl:#f2f6ff}}
@media (prefers-color-scheme:dark){{:root:not([data-theme="light"]){{--bg:#111110;--paper:#1a1a19;--ink:#ecece8;--ink2:#a9a9a2;--line:#33332f;
--goodt:#14301a;--goodd:#7fdc7f;--badt:#3a1a1a;--badd:#ff9d9d;--warnt:#3a2e12;--warnd:#ffd27a;--s1:#6f9bff;--s2:#c08cf0;--hl:#1e2433}}}}
:root[data-theme="dark"]{{--bg:#111110;--paper:#1a1a19;--ink:#ecece8;--ink2:#a9a9a2;--line:#33332f;--goodt:#14301a;--goodd:#7fdc7f;
--badt:#3a1a1a;--badd:#ff9d9d;--warnt:#3a2e12;--warnd:#ffd27a;--s1:#6f9bff;--s2:#c08cf0;--hl:#1e2433}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font:12px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,"Noto Sans Devanagari",sans-serif}}
.page{{max-width:1040px;margin:16px auto;background:var(--paper);border:1px solid var(--line);border-radius:10px;padding:22px 26px}}
h1{{font-size:19px;margin:0}} h2{{font-size:12.5px;margin:14px 0 6px;text-transform:uppercase;letter-spacing:.04em;color:var(--ink2)}}
.sub{{color:var(--ink2);margin:3px 0 0}} .m{{color:var(--ink2);font-size:10.5px}}
.banner{{margin:10px 0 0;padding:6px 10px;border-radius:6px;background:var(--warnt);color:var(--warnd);font-size:11px}}
.tiles{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:14px}}
.tile{{border:1px solid var(--line);border-radius:8px;padding:10px 12px}}
.tile .k{{font-size:10.5px;color:var(--ink2);text-transform:uppercase;letter-spacing:.04em}}
.tile .v{{font-size:26px;font-weight:750;line-height:1.15;margin-top:2px}} .tile .d{{font-size:11px;color:var(--ink2)}}
.grid{{display:grid;grid-template-columns:1.7fr 1fr;gap:16px}} .grid>div,.tile{{min-width:0}} .foot{{overflow-wrap:anywhere}}
table{{width:100%;border-collapse:collapse}} th{{text-align:left;font-size:10.5px;color:var(--ink2);font-weight:600;border-bottom:1px solid var(--line);padding:4px 6px}}
td{{border-bottom:1px solid var(--line);padding:5px 6px;vertical-align:top}} td.n{{text-align:right;white-space:nowrap}} tr.hl td{{background:var(--hl)}}
.chip{{display:inline-block;font-size:10px;font-weight:700;border-radius:9px;padding:1px 7px;background:var(--line);color:var(--ink)}}
.chip.good{{background:var(--goodt);color:var(--goodd)}} .chip.bad{{background:var(--badt);color:var(--badd)}} .chip.warn{{background:var(--warnt);color:var(--warnd)}}
.cm{{display:grid;grid-template-columns:auto 1fr 1fr;gap:3px;font-size:11px}} .cm div{{padding:7px;border-radius:5px;text-align:center}}
.cm .h{{color:var(--ink2);font-size:10.5px;background:none}} .cm .ok{{background:var(--goodt);color:var(--goodd)}} .cm .no{{background:var(--badt);color:var(--badd)}} .cm .fa{{background:var(--warnt);color:var(--warnd)}}
.cm b{{display:block;font-size:20px}}
.bar{{display:flex;height:12px;border-radius:4px;overflow:hidden;gap:2px;margin:6px 0 4px}} .seg{{display:block;height:100%;border-radius:3px}}
.s1{{background:var(--s1)}} .s2{{background:var(--s2)}} .s3{{background:var(--s3)}}
.key{{margin-right:10px;font-size:11px}} .key i{{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:4px;vertical-align:-1px}}
.slide{{border-left:3px solid var(--s1);padding:6px 10px;margin:8px 0 0;font-size:12.5px}}
.ec{{display:inline-block;border:1px solid var(--line);border-radius:5px;padding:1px 6px;margin:0 4px 4px 0;font-size:10.5px}}
ul{{margin:4px 0 0;padding-left:16px}} li{{margin:2px 0}}
.foot{{margin-top:12px;font-size:10px;color:var(--ink2);border-top:1px solid var(--line);padding-top:6px}}
@media (max-width:760px){{.page{{margin:0;border-radius:0;padding:16px}} .tiles{{grid-template-columns:1fr 1fr}} .grid{{grid-template-columns:1fr}}
.wrap{{overflow-x:auto}}}}
@media print{{@page{{size:A4 portrait;margin:7mm}} .heard{{display:none}} .page{{zoom:.8}} body{{background:#fff;font-size:9px}} .m{{font-size:7.6px}} .banner{{font-size:8.3px;padding:4px 8px}} .cm b{{font-size:15px}} .cm div{{padding:4px}} .page{{border:0;margin:0;padding:0;max-width:none}}
.tile .v{{font-size:20px}} h2{{margin:8px 0 4px}} td{{padding:3px 5px}}}}
</style></head><body><div class="page">
<h1>CarePilot voice agent · reply-sorter safety test</h1>
<p class="sub">Does the agent stop when a patient mentions a health concern, and does it stay out of the way when she doesn't?
80 Hindi replies from the pre-demo test pack ({pack}), run on the live Sarvam models, {date}.</p>
<div class="banner"><b>Synthetic test.</b> Every reply was written for this test and spoken by a synthetic voice; no patient is involved.
The numbers show how the system behaves, not patient outcomes. The test is small, so each rate shows the 95% upper bound (“≤”) alongside it.</div>

<div class="tiles">
 <div class="tile"><div class="k">Missed concerns · {head}</div><div class="v">{miss}</div><div class="d"><span class="chip {miss_tone}">{miss_verdict}</span> the dangerous mistake</div></div>
 <div class="tile"><div class="k">Miss rate (recall {recall})</div><div class="v">{miss_rate}</div><div class="d">true rate could be up to {miss_ub}</div></div>
 <div class="tile"><div class="k">False alarms · {head}</div><div class="v">{fa}</div><div class="d">the safe mistake: costs a coordinator a call</div></div>
 <div class="tile"><div class="k">False alarm rate</div><div class="v">{fa_rate}</div><div class="d">true rate could be up to {fa_ub}</div></div>
</div>

<div class="grid">
<div>
<h2>Results by round</h2>
<div class="wrap"><table>
<thead><tr><th>Round</th><th style="text-align:right">Caught</th><th style="text-align:right">Missed</th><th style="text-align:right">Miss rate</th>
<th style="text-align:right">False alarms</th><th style="text-align:right">False alarm rate</th><th style="text-align:right">Development · held-out</th></tr></thead>
<tbody>{rows}</tbody></table></div>
<p class="m">Counting (pack §7): a concern line counts as caught only if <b>every</b> run stopped; a no-concern line is a false alarm if 2 of 3 runs (or its single run) stopped.
The headline is {head} ({head_what}), because the demo uses speech. 20 held-out lines were never used to change anything.</p>
</div>
<div>
<h2>{head}: what happened to each reply</h2>
<div class="cm">
 <div class="h"></div><div class="h">Mentions a concern</div><div class="h">No concern</div>
 <div class="h" style="text-align:right">Agent stopped</div><div class="ok"><b>{cm_c}</b>caught</div><div class="fa"><b>{cm_f}</b>false alarm</div>
 <div class="h" style="text-align:right">Agent carried on</div><div class="no"><b>{cm_m}</b>missed</div><div class="ok"><b>{cm_r}</b>right</div>
</div>
<h2>Why it stopped (caught concerns, {head})</h2>
<div class="bar">{bar}</div><div>{legend}</div>
<p class="m">Health is checked twice: a fixed word list on her words and the translation first, then the model. Either one alone is enough to stop the call.</p>
</div></div>

{fix}
<h2>Every miss and false alarm, and examples that went right</h2>
<div class="wrap"><table>
<thead><tr><th>Line</th><th>What she said</th><th>AI translation</th><th>Labels per run · why</th><th>Result</th></tr></thead>
<tbody>{samples}</tbody></table></div>

<div class="grid">
<div>
<h2>What this means</h2>
<div class="slide">{slide}</div>
<ul>
<li><b>Explainable:</b> the AI only picks one of four labels; fixed rules decide the next step, and every stop records its trigger (shown above).</li>
<li><b>Fails safe:</b> {faults_text} On a live call any technical fault is a safety stop handed to a person (tests/test_tech_exceptions.py).</li>
<li><b>Stable:</b> {n_unstable} line(s) changed label between runs: {unstable}.</li>
<li><b>Speed:</b> median {med} s from her reply to the label (95th percentile {p95} s); the call allows 16 s before it stops safely.</li>
</ul>
</div>
<div>
<h2>Edge cases (not scored; Dr. Sekhar to decide)</h2>
<table><thead><tr><th></th><th>She says</th><th>Got</th><th>Suggested in the pack</th></tr></thead><tbody>{edge}</tbody></table>
<h2>Not shown, on purpose</h2>
<p class="m" style="margin:0">No overall accuracy percentage, no patient decline rate and no benefit figure (pack §12): on 80 synthetic lines they would overstate what is known.</p>
</div></div>

<div class="foot">Frozen for the run: code {commit} · speech-to-text {stt} · translation {mt} · sorter {llm}, prompt {prompt} · voice for rounds B/C {voice} (stand-in until a Hindi speaker records the lines) ·
plan “{plan}” (pack §5: A ×3 for 80, B ×1 for 80, C ×1 for the 40 concern lines) · {runs} runs. Reproduce: <code>python scripts/predemo_pack.py run</code>, then <code>report</code>.</div>
</div></body></html>
"""
