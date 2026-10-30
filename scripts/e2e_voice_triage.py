"""End-to-end UI run of Voice Triage MVP-lite in a real browser, recorded as one video.

Drives the page as each person (coordinator, Doctor on Duty, Senior Oncologist, ER duty nurse, ER doctor),
runs Scenarios A-D, the edge cases and the demo script of CarePilot_Voice_Triage_MVP_Lite_v1.1, and writes:

    e2e-results/voice_triage_e2e.mp4     the whole run, with each test case and its PASS / FAIL on screen
    e2e-results/report.html              results table with video timestamps and screenshots
    e2e-results/results.json

Needs the local demo running (python scripts/local_demo.py) and Playwright:

    pip install playwright && python -m playwright install chromium
    python scripts/e2e_voice_triage.py [--headless] [--pytest-xml path]
"""
import argparse
import html
import json
import re
import shutil
import subprocess
import sys
import time
import traceback
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "e2e-results"
PAGE, API = "http://127.0.0.1:5173/", "http://127.0.0.1:8000/"
LAKSHMI = "Lakshmi V."
T = 20_000  # ms to wait for anything on screen

CAPTION = """
(() => {
  if (window.__cap) return;
  // Count the voices playing at once (TC-30: one voice at a time).
  window.__playing = 0;
  window.__maxPlaying = 0;
  const play = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function (...args) {
    if (!this.__tracked) {
      this.__tracked = true;
      const off = () => { if (this.__on) { this.__on = false; window.__playing -= 1; } };
      this.addEventListener('playing', () => {
        if (!this.__on) { this.__on = true; window.__playing += 1; }
        window.__maxPlaying = Math.max(window.__maxPlaying, window.__playing);
      });
      ['pause', 'ended', 'emptied', 'error'].forEach((ev) => this.addEventListener(ev, off));
    }
    return play.apply(this, args);
  };
  const make = () => {
    const box = document.createElement('div');
    box.id = '__e2e_cap';
    box.style.cssText = 'position:fixed;left:12px;bottom:12px;z-index:2147483647;max-width:760px;padding:10px 14px;'
      + 'border-radius:10px;font:600 14px/1.4 system-ui,sans-serif;color:#fff;background:rgba(17,24,39,.92);'
      + 'box-shadow:0 6px 24px rgba(0,0,0,.35);pointer-events:none;white-space:pre-line';
    document.body.appendChild(box);
    return box;
  };
  window.__cap = (text, status) => {
    const box = document.getElementById('__e2e_cap') || make();
    box.textContent = text;
    box.style.background = status === 'PASS' ? 'rgba(21,128,61,.95)' : status === 'FAIL' ? 'rgba(185,28,28,.95)'
      : 'rgba(17,24,39,.92)';
  };
  window.__card = (inner) => {
    let c = document.getElementById('__e2e_card');
    if (!inner) { if (c) c.remove(); return; }
    if (!c) { c = document.createElement('div'); c.id = '__e2e_card'; document.body.appendChild(c); }
    c.style.cssText = 'position:fixed;inset:0;z-index:2147483646;background:rgba(15,23,42,.96);color:#fff;'
      + 'display:flex;align-items:center;justify-content:center;font:15px/1.5 system-ui,sans-serif;overflow:auto';
    c.innerHTML = inner;
  };
})();
"""


class Run:
    def __init__(self, page, shots: Path):
        self.page, self.shots, self.results, self.t0 = page, shots, [], time.monotonic()

    def at(self) -> float:
        return round(time.monotonic() - self.t0, 1)

    def cap(self, text, status=None):
        try:
            self.page.evaluate("([t, s]) => window.__cap && window.__cap(t, s)", [text, status])
        except Exception:
            pass

    def case(self, tid, title, persona, ref, expected, fn):
        start = self.at()
        self.cap(f"{tid} · {title}\nAs: {persona}\nExpect: {expected}")
        status, error = "PASS", ""
        try:
            fn()
        except Exception as e:  # a failed check is recorded and the run goes on
            status = "FAIL"
            error = f"{type(e).__name__}: {str(e).splitlines()[0][:400]}"
            print(traceback.format_exc(limit=2), file=sys.stderr)
        shot = self.shots / f"{tid}.png"
        try:
            self.page.screenshot(path=str(shot))
        except Exception:
            shot = None
        self.cap(f"{tid} · {title} — {status}" + (f"\n{error[:220]}" if error else ""), status)
        self.page.wait_for_timeout(1400)
        self.results.append({"id": tid, "title": title, "persona": persona, "ref": ref, "expected": expected,
                             "status": status, "error": error, "video_at": start, "end_at": self.at(),
                             "screenshot": shot.name if shot else None})
        print(f"{status}  {tid}  {title}" + (f"  -- {error}" if error else ""), flush=True)


def api_get(path, user):
    req = urllib.request.Request(API + path, headers={"X-User": user})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read(), r.headers.get("content-type")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--pytest-xml", help="JUnit XML from the pytest run, shown in the report")
    args = ap.parse_args()
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "screens").mkdir(parents=True)
    raw = OUT / "raw"

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=args.headless, slow_mo=120, args=["--window-size=1460,1000", "--autoplay-policy=no-user-gesture-required"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, record_video_dir=str(raw),
                                  record_video_size={"width": 1440, "height": 900})
        ctx.add_init_script(CAPTION)
        page = ctx.new_page()
        page.set_default_timeout(T)
        run = Run(page, OUT / "screens")
        page.goto(PAGE)
        page.wait_for_selector("#roleSelect")
        page.evaluate(CAPTION)
        page.evaluate("""window.__card('<div style="max-width:820px;text-align:center"><div style="font-size:30px;font-weight:800">'
          + 'CarePilot · Voice Triage MVP-lite</div><div style="margin-top:10px;font-size:17px">End-to-end UI test run · '
          + 'CarePilot_Voice_Triage_MVP_Lite_v1.1</div><div style="margin-top:16px;opacity:.85">Scenarios A–D, edge cases EC-A, '
          + 'EC-C, EC-D, EC-E, the coordinator call results, the doctor\\'s order, ER Desk, follow-up, Pre-OPD brief and the '
          + 'Golden Path. Personas: Anita (coordinator), Dr. Ananya (Doctor on Duty), Dr. Sekhar (Senior Oncologist), '
          + 'Nurse Deepa and Dr. Farhan (ER Desk). SYNTHETIC DATA ONLY.</div></div>')""")
        page.wait_for_timeout(3500)
        page.evaluate("window.__card(null)")
        steps(page, run)
        summary_card(page, run)
        video = page.video
        ctx.close()
        browser.close()
        webm = Path(video.path())

    mp4 = OUT / "voice_triage_e2e.mp4"
    ff = shutil.which("ffmpeg")
    if ff:
        subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(webm), "-c:v", "libx264", "-preset", "veryfast",
                        "-crf", "30", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(mp4)], check=True)
    else:
        mp4 = OUT / "voice_triage_e2e.webm"
        shutil.copy(webm, mp4)
    shutil.rmtree(raw, ignore_errors=True)
    unit = pytest_results(args.pytest_xml)
    (OUT / "results.json").write_text(json.dumps({"ui": run.results, "pytest": unit}, indent=2), encoding="utf-8")
    report(run.results, unit, mp4.name)
    passed = sum(r["status"] == "PASS" for r in run.results)
    print(f"\n{passed}/{len(run.results)} UI test cases passed. Video: {mp4}\nReport: {OUT / 'report.html'}")
    return 0 if passed == len(run.results) else 1


# ---------------------------------------------------------------- the run

def steps(page, run: Run):
    def persona(uid):
        close_modal()
        page.select_option("#roleSelect", uid)
        page.wait_for_timeout(900)

    def tab(name):
        close_modal()
        page.locator(".navtab", has_text=name).first.click()
        page.wait_for_timeout(700)

    def close_modal():
        x = page.locator(".modal-close")
        if x.count() and x.first.is_visible():
            x.first.click()
            page.wait_for_timeout(300)

    def toast(pattern):
        expect(page.locator("#toast")).to_contain_text(re.compile(pattern), timeout=T)

    def reset(label, then_as):
        close_modal()
        page.get_by_role("button", name="Reset demo").click()
        page.locator(".modal").get_by_role("button", name=label).click()
        toast("Demo reset")
        page.wait_for_timeout(1200)
        persona(then_as)

    def messages(name=LAKSHMI):
        tab("Workspace")
        page.locator("#psq").fill(name)  # "Find a patient" opens her page
        page.get_by_role("button", name="Messages", exact=True).click()
        expect(page.locator(".modal")).to_be_visible()
        pane = page.locator(".vt-call")
        expect(pane).to_be_visible()
        return pane

    def voice_note(name=LAKSHMI):
        pane = messages(name)
        page.locator("#msgV").select_option("TA-01")
        page.get_by_role("button", name="Send voice note").click()
        expect(pane.locator(".vt-ring")).to_contain_text("Incoming call", timeout=60_000)
        return pane

    def answer(pane, q, label):
        box = pane.locator(".vt-q").first
        # The label runs on into its chips: "Question 5bhistory", "Question 1Red question".
        expect(box.locator(".small-note").first).to_contain_text(re.compile(rf"Question {q}(history|Red question|$)"),
                                                                 timeout=60_000)
        box.get_by_role("button", name=label, exact=True).click()

    def tier(pane, text):
        expect(pane.locator(".card-title").first.locator(".pill")).to_contain_text(text, timeout=60_000)

    def replay(pane, scenario):
        pane.locator("select").first.select_option(scenario)
        pane.get_by_role("button", name=re.compile("^(Replay the call as|Play as)$")).click()

    YES, NO, DK = "Yes · ஆம்", "No · இல்லை", "Don't know · தெரியவில்லை"
    state = {}

    # -- set-up and Scenario A
    def tc01():
        # Any data set may be loaded: reset as whichever coordinator it has.
        first = page.locator("#roleSelect option", has_text="Care coordinator").first.get_attribute("value")
        persona(first)
        reset("Reset to Voice Triage", "u_anita")
        expect(page.locator("#roleSelect")).to_contain_text("Anita")
        tab("Workspace")
        expect(page.get_by_text(LAKSHMI).first).to_be_visible()
        expect(page.locator(".demo-bar")).to_contain_text("27 Oct 2026")
    run.case("TC-01", "Reset to the Voice Triage data set", "Anita · coordinator", "§1.3, §7 scene 1",
             "Lakshmi V. (SYN-0142) loaded, demo clock 27 Oct 2026", tc01)

    def tc02():
        state["pane"] = voice_note()
        chat = page.locator(".modal")
        expect(chat).to_contain_text(re.compile("sleepy|தூக்க"))  # her words, transcribed
        expect(state["pane"]).to_contain_text("SYNTHETIC DEMO")
        expect(state["pane"].locator(".vt-ring")).to_contain_text("due by")
    run.case("TC-02", "Voice note TA-01 brings the acknowledgement and a call due within 2 minutes",
             "Anita · phone simulator", "§7 scene 2, §3.1", "Tamil words + English translation, no handover, call due", tc02)

    def tc03():
        pane = state["pane"]
        pane.get_by_role("button", name="Pick up").click()
        answer(pane, 1, NO)
        answer(pane, 2, NO)
        answer(pane, 3, "98.4 °F")
        answer(pane, 4, "0")
        answer(pane, 5, YES)
        answer(pane, "5a", "5")
        answer(pane, "5b", YES)
        answer(pane, 6, YES)
        answer(pane, 7, YES)
        answer(pane, "7a", YES)
        tier(pane, "Amber")
        expect(pane.locator(".vt-table").first.locator(".tag", has_text="history")).to_have_count(3)
    run.case("TC-03", "Scenario A: the call answered by hand, question by question", "Anita (playing Lakshmi)",
             "§3.4, §7 scene 3", "10 questions in Tamil; 5b, 6 and 7a carry a history chip; result Amber", tc03)

    def tc04():
        why = state["pane"].locator(".vt-why")
        expect(why).to_contain_text("Why this tier")
        for code in ("AMBER_BANGLES_TIGHTER_THAN_BASELINE", "AMBER_HEADACHE_RISE_2_PLUS",
                     "AMBER_HEADACHE_WORSE_THAN_LAST_TIME", "AMBER_NAUSEA_DESPITE_MEALS"):
            expect(why).to_contain_text(code)
        expect(why).to_contain_text("10 Oct")
        expect(why).to_contain_text("DRAFT_UNSIGNED")
    run.case("TC-04", "Why this tier compares today with her history", "Anita · phone simulator",
             "§4.3, §7 scene 4", "Four Amber reasons; bangles 10 Oct vs today; headache 2 then 5; DEMO_LITE / DRAFT_UNSIGNED", tc04)

    def tc05():
        tab("Voice triage")
        card = page.locator(".card.a-warn").first
        expect(card).to_contain_text(LAKSHMI)
        expect(page.locator(".vt-synth").first).to_be_visible()
        card.get_by_role("button", name="Acknowledge").click()
        toast("Acknowledged")
        expect(page.locator(".card.a-warn").first).to_contain_text("Acknowledged by")
    run.case("TC-05", "Coordinator sees the Amber card and acknowledges it", "Anita · coordinator", "§6 Coordinator",
             "Amber card in Needs you; Acknowledge recorded", tc05)

    def tc06():
        persona("u_ananya")
        tab("Voice triage")
        expect(page.locator(".screen-eyebrow")).to_contain_text("Doctor on Duty")
        card = page.locator(".card.a-warn").first
        expect(card.locator(".vt-sbar")).to_contain_text("Callback by")
        expect(card.locator(".vt-why")).to_be_visible()
        card.get_by_role("button", name="Physiotherapy referral").click()
        toast("order card created")
        expect(page.locator(".card.a-warn").first).to_contain_text("Order: Physiotherapy referral")
    run.case("TC-06", "Doctor on Duty orders a physiotherapy referral from the SBAR card", "Dr. Ananya · Doctor on Duty",
             "§7 scene 5, §6 Doctor on Duty", "SBAR with Why and the 2-hour clock; order recorded", tc06)

    def tc07():
        persona("u_anita")
        page.locator(".navtab", has_text="Voice triage").first.click()
        page.locator(".card.a-warn").first.get_by_role("button", name=LAKSHMI).click()
        expect(page.locator(".wrap")).to_contain_text("Physiotherapy referral")
        pane = messages()
        expect(page.locator(".modal .wa-phone, .modal .wa-chat, .modal").first).to_contain_text(re.compile("[Pp]hysio|பிசியோ"))
        state["pane"] = pane
    run.case("TC-07", "The order card and the booking message reach her", "Anita · coordinator",
             "§7 scene 5", "Physiotherapy referral card on her plan; booking message on her WhatsApp", tc07)

    def tc08():
        pane = state["pane"]
        replay(pane, "green")
        tier(pane, "Green")
        expect(pane.locator(".vt-out")).to_contain_text("signed sentence was repeated")
        close_modal()
        tab("Voice triage")
        page.get_by_role("button", name=re.compile("Green digest")).click()
        expect(page.locator(".card")).to_contain_text(LAKSHMI)
    run.case("TC-08", "Scenario B: Play as Green", "Anita · phone simulator", "§3.5, §7 scene 6a",
             "Green; the doctor's own 19 Oct sentence repeated; a digest line, no alert", tc08)

    def tc09():
        pane = messages()
        replay(pane, "red")
        tier(pane, "Red · CONFIRMED")
        table = pane.locator(".vt-table").first
        expect(table.locator("tr", has_text="Do you have a headache")).to_contain_text("not asked")
        expect(pane.locator(".vt-out")).to_contain_text("Emergency package sent")
        expect(page.locator(".modal .wa-doc", has_text="emergency_summary.pdf")).to_be_visible()
        state["pane"] = pane
    run.case("TC-09", "Scenario C: Play as Red stops at question 2 and sends the package", "Anita · phone simulator",
             "§3.6, §5, §7 scene 6b", "Red CONFIRMED; later questions not asked; family message with the PDF", tc09)

    def tc10():
        with page.context.expect_page() as popup:
            page.locator(".modal .wa-doc", has_text="emergency_summary.pdf").click()
        pdf = popup.value
        pdf.wait_for_load_state()
        assert pdf.url.startswith("blob:"), pdf.url
        page.wait_for_timeout(1500)
        pdf.close()
        eps = json.loads(api_get("triage/episodes", "u_anita")[0])
        doc = eps[0]["pdf_doc_id"]
        data, ctype = api_get(f"triage/documents/{doc}", "u_er_doctor")
        assert ctype.startswith("application/pdf") and data[:4] == b"%PDF", ctype
        assert data.count(b"/Type /Page") - data.count(b"/Type /Pages") == 1, "PDF is not one page"
        assert b"SYNTHETIC DEMO" in data
        for banned in (b"we recommend", b"maps.google", b"google.com/maps"):
            assert banned not in data.lower(), banned
    run.case("TC-10", "The emergency summary PDF opens and is one page", "Anita · family's WhatsApp",
             "§5.4, §9.2", "PDF opens from the chat; one page; SYNTHETIC DEMO; no recommendation or map link", tc10)

    def tc11():
        close_modal()
        tab("Voice triage")
        card = page.locator(".card.a-bad").first
        expect(card).to_contain_text("Red · CONFIRMED")
        expect(card.locator(".vt-task")).to_contain_text("Task (due")
        expect(card.get_by_role("button", name="Emergency summary PDF")).to_be_visible()
    run.case("TC-11", "Coordinator gets the confirm-emergency-visit task", "Anita · coordinator", "§5.5",
             "Red card with the task due in 30 minutes and the PDF link", tc11)

    def tc12():
        persona("u_er_nurse")
        expect(page.locator(".navtab")).to_have_count(1)
        expect(page.locator(".screen-title")).to_contain_text("Red alerts")
        card = page.locator(".card.a-bad").first
        expect(card).to_contain_text("CONFIRMED")
        expect(card.locator(".vt-note")).to_contain_text("RED alert")
        expect(page.locator(".vt-synth").first).to_be_visible()
        card.get_by_role("button", name="Acknowledge").click()
        toast("Acknowledged")
        expect(page.locator(".card.a-bad").first).to_contain_text("Acknowledged at the ER Desk")
        expect(page.locator(".card.a-bad").first).to_contain_text("Only the ER doctor closes a Red case")
    run.case("TC-12", "ER Desk duty nurse sees the Red alert and acknowledges", "Nurse Deepa · ER Desk",
             "§6 ER Desk", "Only the ER Desk tab; doctor note and PDF; acknowledge; cannot close", tc12)

    def tc13():
        persona("u_sekhar")
        tab("Voice triage")
        expect(page.locator(".screen-eyebrow")).to_contain_text("Senior Oncologist")
        banner = page.locator(".vt-banner").first
        expect(banner).to_contain_text("RED alert")
        banner.get_by_role("button", name="Pre-OPD brief").click()
        modal = page.locator(".modal")
        expect(modal).to_contain_text("Pre-OPD brief")
        expect(modal.locator(".vt-table tbody tr")).not_to_have_count(0)
        expect(modal).to_contain_text("10 Oct 2026")
    run.case("TC-13", "Senior Oncologist sees the Red banner and the Pre-OPD brief", "Dr. Sekhar · Senior Oncologist",
             "§6 Senior Oncologist, §7 scene 1", "Red banner; 90-day brief incl. the 10 and 19 Oct history", tc13)

    def tc14():
        persona("u_anita")
        tab("Voice triage")
        task = page.locator(".card.a-bad").first.locator(".vt-task")
        task.locator("select").select_option("NOT_YET")
        task.get_by_role("button", name="Log").click()
        toast("NOT YET")
        task = page.locator(".card.a-bad").first.locator(".vt-task")
        task.locator("select").select_option("REACHED")
        task.get_by_placeholder("Hospital name").fill("City General Hospital (synthetic)")
        task.get_by_role("button", name="Log").click()
        toast("REACHED")
        card = page.locator(".card.a-bad").first
        expect(card).to_contain_text("NOT YET")
        expect(card).to_contain_text("City General Hospital (synthetic)")
        expect(card.locator(".vt-task")).to_have_count(0)
        expect(card).to_contain_text("Red · CONFIRMED")  # the coordinator's call does not close it
    run.case("TC-14", "Coordinator logs the family calls: NOT YET, then REACHED with the hospital", "Anita · coordinator",
             "§5.6", "Both calls logged; task done; the Red case stays open", tc14)

    def tc15():
        persona("u_er_doctor")
        card = page.locator(".card.a-bad").first
        card.get_by_placeholder("ER doctor's closing note").fill("Seen at City General; arm cellulitis ruled out (synthetic).")
        card.get_by_role("button", name="Close case").click()
        toast("closed by the ER doctor")
        expect(page.locator(".card", has_text="Closed Red cases")).to_contain_text(LAKSHMI)
    run.case("TC-15", "Only the ER doctor closes the Red case", "Dr. Farhan · ER doctor", "§5.6, §6 ER Desk",
             "Case closed with a note; listed under Closed Red cases", tc15)

    # -- Scenario D: no answer
    def tc16():
        persona("u_anita")
        pane = voice_note()
        before = page.locator(".demo-bar").inner_text()
        pane.get_by_role("button", name="She does not answer").click()
        toast("next call is in 5 minutes")
        expect(pane.locator(".vt-ring")).to_contain_text("second call")
        pane.get_by_role("button", name="She does not answer").click()
        tier(pane, "Red · NO ANSWER")
        assert page.locator(".demo-bar").inner_text() != before, "the clock did not move"
        expect(pane.locator(".vt-out")).to_contain_text("Emergency package sent")
        eps = json.loads(api_get("triage/episodes", "u_anita")[0])
        data, _ = api_get(f"triage/documents/{eps[0]['pdf_doc_id']}", "u_er_doctor")
        assert b"Her voice message" in data and b"machine translation" in data, "voice note missing from the PDF"
        state["pane"] = pane
    run.case("TC-16", "Scenario D: she does not answer twice", "Anita · phone simulator", "§3.7, §7 scene 6c",
             "Clock moves 5 min; Red NO ANSWER; same package; PDF shows her translated voice message", tc16)

    # -- EC-A: unconfirmed
    def tc17():
        pane = state["pane"]
        replay(pane, "unclear_arm")
        tier(pane, "Red · UNCONFIRMED")
        expect(pane.locator(".vt-out")).to_contain_text("no family message")
    run.case("TC-17", "EC-A: \"I don't know\" twice on the arm question", "Anita · phone simulator", "§3.8 EC-A",
             "Red UNCONFIRMED; nurse callback in 15 min; no automatic family message", tc17)

    def tc18():
        persona("u_er_nurse")
        card = page.locator(".card.a-bad").first
        expect(card).to_contain_text("UNCONFIRMED")
        card.get_by_placeholder("What the nurse found on the call").fill("Spoke to Karthik: arm looks normal, unsure (synthetic).")
        card.get_by_role("button", name="Record nurse callback").click()
        toast("Nurse callback recorded")
        page.locator(".card.a-bad").first.get_by_role("button", name="Send emergency package").click()
        toast("Emergency package sent")
        expect(page.locator(".card.a-bad").first.locator(".vt-note")).to_contain_text("RED alert")
    run.case("TC-18", "ER Desk records the nurse callback and sends the package by hand", "Nurse Deepa · ER Desk",
             "§5.2, §6 ER Desk", "Callback note saved; Send emergency package (UNCONFIRMED only) works", tc18)

    # -- keypad 0
    def tc19():
        persona("u_anita")
        pane = messages()
        replay(pane, "nurse")
        expect(pane.locator(".vt-turns")).to_contain_text("pressed 0")
        close_modal()
        persona("u_er_nurse")
        expect(page.locator(".card-title", has_text="Nurse callbacks")).to_be_visible()
        expect(page.locator(".card.a-warn").first).to_contain_text("Call back by")
    run.case("TC-19", "She presses 0 for a nurse", "Anita, then Nurse Deepa", "§3.1",
             "Call ends; a nurse callback card on the ER Desk", tc19)

    # -- EC-C and a doctor's tier change, closure, reopen
    def tc20():
        persona("u_anita")
        pane = messages()
        replay(pane, "no_thermometer")
        tier(pane, "Amber")
        expect(pane.locator(".vt-why")).to_contain_text("AMBER_NO_THERMOMETER_WITH_SYMPTOMS")
    run.case("TC-20", "EC-C: no thermometer, with a headache", "Anita · phone simulator", "§3.8 EC-C",
             "Amber, AMBER_NO_THERMOMETER_WITH_SYMPTOMS", tc20)

    def tc21():
        persona("u_ananya")
        tab("Voice triage")
        card = page.locator(".card.a-warn").first
        card.locator("select").select_option("TIER_3_GREEN")
        card.get_by_placeholder("Reason (required)").fill("Spoke to her: has a thermometer at her daughter's, normal (synthetic)")
        card.get_by_role("button", name="Change tier").click()
        toast("Tier changed")
        page.get_by_role("button", name=re.compile("Green digest")).click()
        item = page.locator(".v4-item", has_text=LAKSHMI).first
        item.get_by_placeholder(re.compile("Closing note")).fill("Lowered to Green after a call; routine follow-up.")
        item.get_by_role("button", name="Confirm closure").click()
        toast("Closure confirmed")
    run.case("TC-21", "Doctor lowers the tier with a reason, then confirms closure", "Dr. Ananya · Doctor on Duty",
             "§6 Doctor on Duty", "Tier change needs a reason; Amber → Green; closure confirmed", tc21)

    def tc22():
        persona("u_anita")
        tab("Voice triage")
        page.get_by_role("button", name=re.compile("^Closed")).click()
        item = page.locator(".v4-item", has_text="Lowered to Green").first
        item.get_by_placeholder("Reason to reopen").fill("Karthik called: headache back this evening (synthetic).")
        item.get_by_role("button", name="Reopen").click()
        toast("reopened")
        page.get_by_role("button", name=re.compile("Green digest")).click()
        expect(page.locator(".card")).to_contain_text(LAKSHMI)
        persona("u_ananya")
        tab("Voice triage")
        page.get_by_role("button", name=re.compile("Green digest")).click()
        item = page.locator(".v4-item", has_text=LAKSHMI).first
        item.get_by_placeholder(re.compile("Closing note")).fill("Reviewed again; no change.")
        item.get_by_role("button", name="Confirm closure").click()
        toast("Closure confirmed")
    run.case("TC-22", "Coordinator reopens a closed episode", "Anita, then Dr. Ananya", "§6 Coordinator",
             "Reopen needs a reason; the episode is back; the doctor closes it again", tc22)

    # -- EC-E: Sarvam off mid-call
    def tc23():
        persona("u_anita")
        pane = voice_note()
        pane.get_by_role("button", name="Pick up").click()
        answer(pane, 1, NO)
        close_modal()
        page.locator(".demo-bar label", has_text="Sarvam").locator("input").click()
        toast("Sarvam switched off")
        pane = messages()
        box = pane.locator(".vt-q").first
        box.get_by_placeholder(re.compile("Or type what she says")).fill("அது வந்து… கொஞ்சம் மாதிரி")
        box.get_by_role("button", name="Send words").click()
        tier(pane, "Red")
        expect(pane.locator(".vt-why")).to_contain_text("RED_SYSTEM_FAULT")
        close_modal()
        page.locator(".demo-bar label", has_text="Sarvam").locator("input").click()
        toast("Sarvam switched on")
    run.case("TC-23", "EC-E: the AI fails mid-call", "Anita · phone simulator", "§3.8 EC-E, §9 fail-closed",
             "Words that need the model, with Sarvam off: Red RED_SYSTEM_FAULT, never a crash, never Green", tc23)

    def close_red_as_er_doctor(note):
        persona("u_er_doctor")
        card = page.locator(".card.a-bad").first
        card.get_by_placeholder("ER doctor's closing note").fill(note)
        card.get_by_role("button", name="Close case").click()
        toast("closed by the ER doctor")
        persona("u_anita")

    # -- EC-D by hand: 100.4 Red, then 100.3 Green
    def tc24():
        close_red_as_er_doctor("System fault case: family reached, she is well (synthetic).")
        pane = voice_note()
        pane.get_by_role("button", name="Pick up").click()
        answer(pane, 1, NO)
        answer(pane, 2, NO)
        box = pane.locator(".vt-q").first
        expect(box.locator(".small-note").first).to_contain_text("Question 3")
        box.get_by_placeholder(re.compile("Or type what she says")).fill("100.4")
        box.get_by_role("button", name="Send words").click()
        tier(pane, "Red · CONFIRMED")
        expect(pane.locator(".vt-why")).to_contain_text("RED_FEVER_100_4F_PLUS")
    run.case("TC-24", "EC-D: temperature 100.4 °F", "Anita (playing Lakshmi)", "§3.8 EC-D",
             "Red at question 3 (RED_FEVER_100_4F_PLUS)", tc24)

    def tc25():
        close_red_as_er_doctor("Fever case: seen, discharged (synthetic).")
        pane = voice_note()
        pane.get_by_role("button", name="Pick up").click()
        answer(pane, 1, NO)
        answer(pane, 2, NO)
        answer(pane, 3, "100.3 °F")
        answer(pane, 4, "0")
        answer(pane, 5, NO)
        answer(pane, 6, NO)
        answer(pane, 7, NO)
        tier(pane, "Green")
        state["pane"] = pane
    run.case("TC-25", "EC-D: temperature 100.3 °F", "Anita (playing Lakshmi)", "§3.8 EC-D",
             "Green: 100.3 is under the 100.4 threshold", tc25)

    # -- Scene 7: next day
    def tc26():
        close_modal()
        page.get_by_role("button", name="Advance 1 day").click()
        toast("28 Oct 2026")
        pane = messages()
        follow = pane.locator(".vt-q", has_text="Follow-up call")
        follow.get_by_role("button", name="Better").click()
        toast("Follow-up: better")
        close_modal()
        persona("u_ananya")
        tab("Voice triage")
        page.get_by_role("button", name=re.compile("Green digest")).click()
        item = page.locator(".v4-item", has_text=LAKSHMI).first
        expect(item).to_contain_text("better")
        item.get_by_placeholder(re.compile("Closing note")).fill("Better on the follow-up call; routine plan.")
        item.get_by_role("button", name="Confirm closure").click()
        toast("Closure confirmed")
    run.case("TC-26", "Scene 7: next-day follow-up call, then the doctor confirms closure", "Anita, then Dr. Ananya",
             "§7 scene 7", "Advance 1 day; follow-up “better”; closure confirmed", tc26)

    def tc27():
        page.get_by_role("button", name="Pre-OPD briefs").click()
        page.locator(".card").get_by_role("button", name=re.compile(LAKSHMI)).click()
        modal = page.locator(".modal")
        expect(modal).to_contain_text("Pre-OPD brief")
        expect(modal.locator(".vt-table tbody tr")).not_to_have_count(0)
        modal.get_by_role("button", name=re.compile("transcript", re.I)).first.click()
        expect(page.locator(".modal")).to_contain_text("Triage call #")
        expect(page.locator(".modal .vt-turns")).to_be_visible()
    run.case("TC-27", "Pre-OPD brief: every line has a source", "Dr. Ananya · oncologist", "§7 scene 7, §6",
             "Brief lists the episodes; a source opens the call transcript", tc27)

    def tc28():
        persona("u_anita")
        tab("Audit trail")
        expect(page.locator(".wrap")).to_contain_text(re.compile("triage", re.I))
    run.case("TC-28", "Every step is in the audit trail", "Anita · coordinator", "§9.2",
             "Audit trail shows the triage events", tc28)

    # -- Golden Path
    def tc29():
        reset("Reset to Golden Path", "u_priya")
        tab("Voice triage")
        expect(page.locator(".screen-title")).to_contain_text("Voice triage")
        voice_note("Savitri Patil")
        close_modal()
        tab("Voice triage")
        expect(page.locator(".card.a-blue").first).to_contain_text("Savitri Patil")
    run.case("TC-29", "Golden Path: a health message goes to Voice Triage", "Priya · coordinator (Golden Path)",
             "Scope: New data set + Golden Path", "Savitri's voice note brings a triage call, not a handover", tc29)

    def tc30():
        pane = messages("Savitri Patil")
        voice = pane.locator("input[type=checkbox]")
        if not voice.is_checked():
            voice.check()
        pane.get_by_role("button", name="Pick up").click()
        # Wait for Bulbul to start the opening line, then answer straight away, before the questions are spoken.
        page.wait_for_function("window.__playing >= 1", timeout=60_000)
        page.evaluate("window.__maxPlaying = window.__playing")
        answer(pane, 1, NO)
        page.wait_for_timeout(500)
        assert page.evaluate("window.__playing") <= 1, "the old line kept playing after the answer"
        page.wait_for_function("window.__playing >= 1", timeout=60_000)  # the next question is spoken
        answer(pane, 2, NO)
        page.wait_for_timeout(6000)
        most = page.evaluate("window.__maxPlaying")
        assert most <= 1, f"{most} voices played at the same time"
        expect(pane.locator(".vt-q").first.locator(".small-note").first).to_contain_text("Question 3")
        close_modal()
        page.wait_for_timeout(500)
        assert page.evaluate("window.__playing") == 0, "the voice kept playing after the pop-up closed"
    run.case("TC-30", "Answering before the question finishes: one voice at a time", "Priya (playing Savitri)",
             "Bug: overlapping voices", "The voice stops at the answer; never two voices; closing the pop-up stops it", tc30)



def summary_card(page, run: Run):
    rows = "".join(
        f"<tr><td style='padding:3px 10px'>{r['id']}</td><td style='padding:3px 10px'>{html.escape(r['title'])}</td>"
        f"<td style='padding:3px 10px;font-weight:800;color:{'#4ade80' if r['status'] == 'PASS' else '#f87171'}'>{r['status']}</td></tr>"
        for r in run.results)
    passed = sum(r["status"] == "PASS" for r in run.results)
    page.evaluate("window.__cap('', null)")
    page.evaluate("(h) => window.__card(h)",
                  f"<div style='padding:20px'><div style='font-size:24px;font-weight:800;margin-bottom:8px'>Result: "
                  f"{passed} of {len(run.results)} UI test cases passed</div><table style='font-size:13px'>{rows}</table></div>")
    page.wait_for_timeout(7000)


def pytest_results(path):
    if not path or not Path(path).exists():
        return None
    root = ET.parse(path).getroot()
    out = []
    for c in root.iter("testcase"):
        status = "FAIL" if c.find("failure") is not None or c.find("error") is not None else (
            "SKIP" if c.find("skipped") is not None else "PASS")
        out.append({"file": c.get("classname", ""), "name": c.get("name"), "status": status,
                    "time": float(c.get("time") or 0)})
    return out


def report(results, unit, video):
    passed = sum(r["status"] == "PASS" for r in results)
    rows = []
    for r in results:
        badge = "pass" if r["status"] == "PASS" else "fail"
        rows.append(
            f"<tr class='{badge}'><td><b>{r['id']}</b></td><td>{html.escape(r['title'])}<div class='m'>{html.escape(r['ref'])}</div></td>"
            f"<td>{html.escape(r['persona'])}</td><td>{html.escape(r['expected'])}"
            + (f"<div class='err'>{html.escape(r['error'])}</div>" if r["error"] else "")
            + f"</td><td><span class='b {badge}'>{r['status']}</span></td>"
            + f"<td><a href='#' data-t='{r['video_at']}'>{int(r['video_at'] // 60)}:{int(r['video_at'] % 60):02d}</a></td>"
            + (f"<td><a href='screens/{r['screenshot']}' target='_blank'>view</a></td></tr>" if r["screenshot"] else "<td></td></tr>"))
    unit_html = ""
    if unit:
        vt = [u for u in unit if "voice_triage" in u["file"]]
        up = sum(u["status"] == "PASS" for u in unit)
        unit_html = (f"<h2>Automated tests (pytest)</h2><p>{up} of {len(unit)} passed across the whole suite. "
                     f"Voice Triage tests ({sum(u['status'] == 'PASS' for u in vt)} of {len(vt)} passed):</p><table>"
                     "<tr><th>Test</th><th>Result</th></tr>"
                     + "".join(f"<tr class='{'pass' if u['status'] == 'PASS' else 'fail'}'><td>{html.escape(u['name'])}</td>"
                               f"<td><span class='b {'pass' if u['status'] == 'PASS' else 'fail'}'>{u['status']}</span></td></tr>"
                               for u in vt)
                     + "</table><p class='m'>EC-B (third headache report in 14 days) needs an extra history event that the "
                       "screens cannot add, so it is covered by <code>test_ec_b_third_headache_in_14_days_and_ec_c_no_thermometer</code>.</p>")
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Voice Triage E2E</title><style>
:root{{--bg:#f6f7f9;--fg:#111827;--card:#fff;--line:#e5e7eb;--m:#6b7280;--good:#15803d;--goodt:#e9f8ee;--bad:#b91c1c;--badt:#fcebeb}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0b0f16;--fg:#e5e7eb;--card:#151c28;--line:#293345;--m:#9ca3af;--good:#4ade80;--goodt:#102a19;--bad:#f87171;--badt:#2a1212}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,sans-serif}} main{{max-width:1180px;margin:0 auto;padding:20px 16px}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:17px;margin:26px 0 8px}} .m{{color:var(--m);font-size:12px}}
.sum{{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0}} .k{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 16px}}
.k b{{font-size:22px;display:block}} video{{width:100%;border-radius:10px;background:#000;position:sticky;top:0}}
table{{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}}
th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}} th{{font-size:12px;color:var(--m)}}
.b{{font-weight:800;font-size:12px;padding:2px 9px;border-radius:12px}} .b.pass{{background:var(--goodt);color:var(--good)}} .b.fail{{background:var(--badt);color:var(--bad)}}
.err{{color:var(--bad);font-size:12px;margin-top:4px}} a{{color:inherit}} .wrap{{overflow-x:auto}}
</style></head><body><main>
<h1>Voice Triage MVP-lite · end-to-end UI test run</h1>
<div class="m">CarePilot_Voice_Triage_MVP_Lite_v1.1 · local demo (throwaway database, synthetic data) · run {time.strftime('%Y-%m-%d %H:%M')}</div>
<div class="sum"><div class="k"><b>{passed}/{len(results)}</b>UI test cases passed</div>
<div class="k"><b style="color:var(--bad)">{len(results) - passed}</b>failed</div>
{f'<div class="k"><b>{sum(u["status"] == "PASS" for u in unit)}/{len(unit)}</b>pytest passed</div>' if unit else ''}</div>
<video id="v" controls preload="metadata" src="{video}"></video>
<p class="m">Click a time to jump the video to that test case. Each case is captioned in the video and turns green (PASS) or red (FAIL).</p>
<h2>UI test cases</h2><div class="wrap"><table><tr><th>ID</th><th>Test case</th><th>Persona</th><th>Expected</th><th>Result</th><th>Video</th><th>Screen</th></tr>
{''.join(rows)}</table></div>{unit_html}
</main><script>document.querySelectorAll('a[data-t]').forEach(a=>a.onclick=e=>{{e.preventDefault();const v=document.getElementById('v');v.currentTime=+a.dataset.t;v.play();window.scrollTo({{top:0,behavior:'smooth'}});}});</script></body></html>"""
    (OUT / "report.html").write_text(page, encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
