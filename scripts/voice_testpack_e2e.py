"""Voice test pack, end to end (3 Nov): every clip of the chosen languages through the real app, against a throwaway
local PostgreSQL (no AWS) with live Sarvam hearing the audio. For each clip: reset the Golden Path, send the WAV as
the patient's voice note, run the call with the clip's answer script, then each persona's part to closure:
Red and Amber: the coordinator confirms, the Doctor on Duty acknowledges (Amber: calls her, orders) and closes with
the summary; Green: the coordinator closes with the summary. Writes testpack/report.html (and report.json).

    python scripts/voice_testpack_e2e.py Tamil Hindi
"""
import html
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pgserver

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import voice_testpack as VT  # noqa: E402

PACK = VT.PACK
PRIYA, KAVITA = {"X-User": "u_priya"}, {"X-User": "u_kavita"}
PERSONA = {"u_priya": "Care coordinator · Priya Nair", "u_kavita": "Doctor on Duty · Dr. Kavita Menon"}
TIERS = {"TIER_1_RED": "Red", "TIER_2_AMBER": "Amber", "TIER_3_GREEN": "Green"}
ORDER_FOR = {"A1": "physio", "A2": "bloods", "A3": "physio", "A4": "bloods", "A5": "visit", "A6": "visit"}
RECOMMEND = {
    "Red": "Go to the nearest emergency department now; the family was told and is taking her",
    "A1": "Keep the left arm raised; physiotherapy this week; come in if the redness or swelling increases",
    "A2": "Blood tests brought forward; paracetamol as before; call again if the headache gets worse",
    "A3": "Rest the arm; physiotherapy referral; come in if the pain spreads or the arm swells",
    "A4": "Blood tests for the tiredness; rest and fluids; review with the results",
    "A5": "Come in for a visit today so we can examine the arm",
    "A6": "Come in for a visit to look at it; no change to the medicines",
}
# what each audit row means, by persona (rows not listed are left out of the report)
SAYS = {
    "voice_note_received": "sent a WhatsApp voice note",
    "ai_understanding": "heard the voice note (speech-to-text, translation, tags)",
    "triage_opened": "opened a triage ticket; automated call due in 2 minutes",
    "triage_call_answered": "called her; she answered",
    "triage_result": "set the tier from her answers",
    "red_alert_created": "raised the Red alert",
    "emergency_package_sent": "sent the family the emergency message with the summary PDF",
    "doctor_note_created": "wrote the doctor note",
    "sbar_card_created": "sent the SBAR card to the Doctor on Duty (2-hour callback)",
    "green_digest_line": "added the Green digest line for the coordinator",
    "triage_reviewed": "reviewed the AI's triage",
    "triage_acknowledged": "acknowledged the ticket",
    "doctor_call_attempt": "called her",
    "doctor_order_recorded": "recorded an order",
    "closing_summary_submitted": "submitted the closing summary",
    "red_closed_by_doctor": "closed the ticket", "amber_closed_by_doctor": "closed the ticket",
    "green_closed_after_review": "closed the ticket",
    "standard_reply_sent": "sent the 'not health' standard reply",
}


def who(row: dict) -> str:
    a = row.get("actor_name") or ""
    if a in PERSONA:
        return PERSONA[a]
    if "gateway" in a.lower():
        return "Patient"
    return "CarePilot AI"


def ok(r, what):
    if r.status_code >= 300:
        raise RuntimeError(f"{what}: {r.status_code} {r.text[:200]}")
    return r.json()


def run_clip(client, cid: str, c: dict) -> dict:
    from app import db, seed

    with db.connect() as conn:
        seed.reset(conn, scenario="step_a")
    t0 = time.time()
    wav = (VT.OUT / f"{cid}.wav").read_bytes()
    r = ok(client.post(f"/simulator/{c['mrn']}/voice", headers=PRIYA,
                       files={"file": (f"{cid}.wav", wav, "audio/wav")}), "voice note")
    out = {"clip": cid, "handling": r.get("handling"), "episode_id": r.get("triage_episode_id")}
    eid = out["episode_id"]
    out["health"] = "Health" if eid else ("Non-health" if any("not_health" in h or "standard_reply" in h
                                                                for h in r.get("handling") or []) else "Not triaged")
    if eid:
        s = ok(client.post(f"/triage/episodes/{eid}/pickup", headers=PRIYA), "pickup")
        guard = 0
        while s.get("current") and not s.get("tier") and guard < 40:
            q = s["current"]["id"]
            v = c["answers"].get(q) or VT.calm(next(x for x in __import__("app.triage_rules", fromlist=["R"])
                                                    .questions(VT.histories()[c["mrn"]]) if x.id == q))
            s = ok(client.post(f"/triage/episodes/{eid}/answer", headers=PRIYA, json={"value": v}), f"answer {q}")
            guard += 1
        out.update(tier=TIERS.get(s.get("tier")), red_kind=s.get("red_kind"), reasons=s["result"]["reason_codes"]
                   if s.get("result") else [], why=(s.get("result") or {}).get("why") or [],
                   heard=s["plan"].get("heard"), asked=[x["id"] for x in s["table"] if x.get("asked")],
                   translation=s.get("translation"), transcript=s.get("patient_words"), ticket=s.get("ticket_id"))
        tier = out["tier"]
        if tier in ("Red", "Amber"):
            ok(client.post(f"/triage/episodes/{eid}/review", headers=PRIYA, json={"action": "CONFIRM"}), "review")
            ok(client.post(f"/triage/episodes/{eid}/acknowledge", headers=KAVITA), "acknowledge")
            if tier == "Amber" and not s.get("reaching"):
                ok(client.post(f"/triage/episodes/{eid}/doctor-call", headers=KAVITA,
                               json={"outcome": "PICKED_UP", "note": "Spoke to her; went over her answers"}), "call")
                ok(client.post(f"/triage/episodes/{eid}/order", headers=KAVITA,
                               json={"order": ORDER_FOR.get(c["story"], "none")}), "order")
            rec = RECOMMEND["Red"] if tier == "Red" else RECOMMEND.get(c["story"], "Reviewed on the phone")
            body = {"recommendation": rec, "contact": "PHONE"}
            d = ok(client.post(f"/triage/episodes/{eid}/summary-draft", headers=KAVITA, json=body), "draft")["draft"]
            s = ok(client.post(f"/triage/episodes/{eid}/close", headers=KAVITA, json={**body, "summary": d}), "close")
        elif tier == "Green":
            d = ok(client.post(f"/triage/episodes/{eid}/summary-draft", headers=PRIYA, json={}), "draft")["draft"]
            s = ok(client.post(f"/triage/episodes/{eid}/close", headers=PRIYA, json={"summary": d}), "close")
        out.update(status=s["status"], closed_by=PERSONA.get(s.get("closed_by"), s.get("closed_by")),
                   summary=s.get("close_note"), orders=[o["order"] for o in s.get("orders") or []])
    rows = ok(client.get("/audit", headers=PRIYA, params={"mrn": c["mrn"], "limit": 200}), "audit")["entries"]
    acts = []
    for row in reversed(rows):
        text = SAYS.get(row["action"])
        if not text:
            continue
        if row["action"] == "message_sent":
            continue
        extra = row.get("outcome") or ""
        if row["action"] == "triage_reviewed":
            text += f": {extra.lower()}"
        elif row["action"] == "doctor_call_attempt":
            text += ": picked up" if extra == "PICKED_UP" else ": no answer"
        elif row["action"] == "doctor_order_recorded":
            text += f": {extra}"
        elif row["action"] == "triage_result":
            text += f" ({extra})"
        acts.append({"persona": who(row), "action": text})
    out["actions"] = acts
    out["seconds"] = round(time.time() - t0, 1)
    return out


def report(results: list[dict], langs: list[str]) -> None:
    clips = VT.CLIPS
    tone = {"Red": "red", "Amber": "amber", "Green": "green"}
    for r in results:  # the AI heard nothing (Sarvam down or out of credit): the call fell back to the fixed questions
        r["unheard"] = r["health"] == "Health" and not r.get("heard")
    lost = sum(1 for r in results if r["unheard"])
    match = sum(1 for r in results if r.get("tier") == clips[r["clip"]]["tier"] and not r["unheard"])
    closed = sum(1 for r in results if r.get("status") == "closed")
    health = sum(1 for r in results if r["health"] == "Health")
    body = []
    for r in results:
        c = clips[r["clip"]]
        st = c["style"]
        voice = ", ".join(x for x in (
            f"pace {st['pace']}" if st.get("pace", 1) != 1 else "", f"low pitch {st['pitch']}" if st.get("pitch", 1) != 1
            else "", "feeble" if st.get("muffled") else "", "crying / stammer" if st.get("tremble") else "",
            f"{st['noise']} noise" if st.get("noise") else "") if x) or "clear"
        exp, got = c["tier"], r.get("tier")
        same = got == exp
        acts = "".join(f"<li><b>{html.escape(a['persona'])}</b> — {html.escape(a['action'])}</li>" for a in r["actions"])
        e = lambda x: html.escape(str(x or ""))  # noqa: E731
        warn = ('<div class="warn">Not a valid test: Sarvam returned no tags (HTTP 402, out of credit), so '
                'the call fell back to the fixed questions. Re-run when Sarvam is back.</div>') if r["unheard"] else ""
        body.append(f"""<tr class="{'unheard' if r['unheard'] else ''}">
<td class="clip"><div class="cid">{e(r['clip'])}</div><div class="meta">{e(c['language'])} · {e(c['dialect'])}{' · ' + e(c['caller']) if c['caller'] != 'patient' else ''}</div>
<div class="meta">{e(voice)}</div><audio controls preload="none" src="audio/{e(r['clip'])}.wav"></audio>
<details><summary>What she says</summary><p lang="{'ta' if c['language'] == 'Tamil' else 'hi'}">{e(c['text'])}</p><p class="en">{e(c['meaning'])}</p></details></td>
<td><span class="pill {'ok' if r['health'] == 'Health' else 'bad'}">{e(r['health'])}</span>{warn}
<details><summary>Heard</summary><p>{e(r.get('transcript'))}</p><p class="en">{e(r.get('translation'))}</p><p class="meta">Tags: {e(', '.join(r.get('heard') or []))}</p></details></td>
<td>{'Yes' if r.get('tier') else 'No'}<div class="meta">{e(r.get('ticket'))}</div><div class="meta">{len(r.get('asked') or [])} questions asked</div></td>
<td><span class="pill {tone.get(got, '')}">{e(got or '—')}</span>{(' <span class="meta">(' + e(r['red_kind'].lower()) + ')</span>') if r.get('red_kind') else ''}
<div class="meta">Expected: {e(exp)} {'— not tested' if r['unheard'] else ('✓' if same else '✗')}</div><ul class="why">{''.join(f'<li>{e(w)}</li>' for w in r.get('why') or [])}</ul></td>
<td><ol class="acts">{acts}</ol></td>
<td>{('<span class="pill ok">Closed</span>' if r.get('status') == 'closed' else '<span class="pill bad">' + e(r.get('status') or 'Not closed') + '</span>')}
<div class="meta">{e(r.get('closed_by'))}</div><p class="sum">{e(r.get('summary'))}</p></td>
</tr>""")
    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Voice Test Results</title>
<style>
:root {{ --bg:#f7f7f5; --card:#fff; --ink:#1d1d1b; --muted:#6b6b66; --line:#e3e2dc; --red:#c62828; --redbg:#fdecea;
  --amber:#a15c00; --amberbg:#fff4e0; --green:#1b7a3a; --greenbg:#e6f4ea; --okbg:#e8f0fe; --ok:#1a56b8; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#151514; --card:#1f1f1d; --ink:#ecebe6;
  --muted:#a3a29b; --line:#33322e; --redbg:#3b1715; --red:#ff8a80; --amberbg:#3a2a10; --amber:#ffc56b; --greenbg:#123222;
  --green:#7ad99a; --okbg:#18284a; --ok:#9cbcff; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:14px/1.45 system-ui, "Segoe UI", sans-serif; padding:24px 16px; }}
h1 {{ font-size:22px; margin:0 0 4px; }} .lede {{ color:var(--muted); margin:0 0 16px; max-width:900px; }}
.stats {{ display:flex; gap:12px; flex-wrap:wrap; margin-bottom:16px; }}
.stat {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:10px 14px; min-width:150px; }}
.stat b {{ display:block; font-size:20px; }} .stat span {{ color:var(--muted); font-size:12px; }}
.wrap {{ overflow-x:auto; background:var(--card); border:1px solid var(--line); border-radius:10px; }}
table {{ border-collapse:collapse; width:100%; min-width:1250px; }}
th, td {{ text-align:left; vertical-align:top; padding:10px; border-bottom:1px solid var(--line); }}
th {{ position:sticky; top:0; background:var(--card); font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }}
.cid {{ font-weight:700; font-size:15px; }} .meta {{ color:var(--muted); font-size:12px; }}
audio {{ width:220px; margin-top:6px; }} details summary {{ cursor:pointer; color:var(--ok); font-size:12px; margin-top:4px; }}
details p {{ margin:4px 0; max-width:340px; }} .en {{ color:var(--muted); font-style:italic; }}
.pill {{ display:inline-block; padding:2px 9px; border-radius:999px; font-weight:600; font-size:12px; }}
.pill.red {{ background:var(--redbg); color:var(--red); }} .pill.amber {{ background:var(--amberbg); color:var(--amber); }}
.pill.green {{ background:var(--greenbg); color:var(--green); }} .pill.ok {{ background:var(--okbg); color:var(--ok); }}
.pill.bad {{ background:var(--redbg); color:var(--red); }}
.why, .acts {{ margin:6px 0 0; padding-left:18px; }} .why li, .acts li {{ margin-bottom:3px; }} .acts {{ min-width:330px; }}
.warn {{ margin-top:6px; padding:6px 8px; border-radius:6px; background:var(--amberbg); color:var(--amber); font-size:12px; max-width:240px; }}
tr.unheard td {{ opacity:.75; }}
.sum {{ margin:6px 0 0; max-width:320px; font-size:13px; }}
</style></head><body>
<h1>Voice Test Results</h1>
<p class="lede">Synthetic test data. {len(results)} {' and '.join(langs)} voice notes from the voice test pack, each run end to end on
the app (throwaway local database, live Sarvam hearing the audio): the voice note, the AI's health check, the
automated triage call with the clip's answer script, the tier, then each persona's part through to closure.
Generated {time.strftime('%d %b %Y %H:%M')}.</p>
<div class="stats">
<div class="stat"><b>{health} / {len(results)}</b><span>taken as health messages</span></div>
<div class="stat"><b>{match} / {len(results) - lost}</b><span>tier as expected (of the clips heard)</span></div>
<div class="stat"><b>{lost}</b><span>not heard: Sarvam out of credit</span></div>
<div class="stat"><b>{closed} / {len(results)}</b><span>episodes closed</span></div>
<div class="stat"><b>{sum(1 for r in results if r.get('tier') == 'Red')} · {sum(1 for r in results if r.get('tier') == 'Amber')} · {sum(1 for r in results if r.get('tier') == 'Green')}</b><span>Red · Amber · Green</span></div>
</div>
<div class="wrap"><table>
<thead><tr><th>Voice clip</th><th>Health or non-health</th><th>Triage done</th><th>Red / Amber / Green</th><th>Actions by persona</th><th>Closure of the episode</th></tr></thead>
<tbody>{''.join(body)}</tbody></table></div>
</body></html>
"""
    (PACK / "report.html").write_text(doc, encoding="utf-8")
    (PACK / "report.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"report: {len(results)} clips; health {health}; tier as expected {match} of {len(results) - lost}; "
          f"not heard {lost}; closed {closed}")


def main(langs: list[str]) -> None:
    os.environ["SARVAM_API_KEY"] = VT.sarvam_key()
    os.environ["AI_PROVIDER"] = "sarvam"
    os.environ.pop("CAREPILOT_SEED_DIR", None)
    server = pgserver.get_server(tempfile.mkdtemp(prefix="carepilot_vt_pg_"), cleanup_mode="delete")
    try:
        uri = server.get_uri()
        os.environ["DATABASE_URL"] = uri
        for var in ("DB_HOST", "DB_INSTANCE_ID"):
            os.environ.pop(var, None)
        import subprocess
        subprocess.run([sys.executable, str(ROOT / "db" / "migrate.py")], env=os.environ, check=True,
                       capture_output=True)
        from fastapi.testclient import TestClient

        from app.main import app

        client = TestClient(app)
        results = []
        for cid, c in VT.CLIPS.items():
            if c["language"] not in langs:
                continue
            try:
                r = run_clip(client, cid, c)
            except Exception as e:  # noqa: BLE001
                r = {"clip": cid, "health": "Error", "error": str(e), "actions": []}
            results.append(r)
            print(f"{cid}: {r.get('health')} · {r.get('tier')} (expected {c['tier']}) · {r.get('status')} · "
                  f"{r.get('seconds')} s{' · ' + r['error'] if r.get('error') else ''}", flush=True)
        report(results, langs)
    finally:
        server.cleanup()


if __name__ == "__main__":
    if sys.argv[1:] == ["report"]:  # rebuild report.html from report.json
        rs = json.loads((PACK / "report.json").read_text(encoding="utf-8"))
        report(rs, sorted({VT.CLIPS[r["clip"]]["language"] for r in rs}, key=["Tamil", "Hindi", "Telugu"].index))
    else:
        main(sys.argv[1:] or ["Tamil", "Hindi"])
