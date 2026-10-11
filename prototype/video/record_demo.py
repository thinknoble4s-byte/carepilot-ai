"""Record a narrated click-through video of the CarePilot AI prototype (v7.2).

Pipeline:
  1. Intro: an animated scene (intro.html) narrated with the supplied intro script;
     scene changes are cued on the exact spoken words (TTS word timings).
  2. Walkthrough: each "beat" = one narration line + the clicks shown while it is spoken.
  3. Narration is synthesised with edge-tts (en-IN-NeerjaNeural, Indian English female voice).
  4. Chromium drives the prototype with a visible animated cursor; frames are captured
     through the Chrome DevTools screencast (crisp 1920x1080).
  5. ffmpeg assembles frames (true timing) + narration placed at beat start times.

Usage:  .venv/Scripts/python record_demo.py            full run -> carepilot_v7.2_demo.mp4
        .venv/Scripts/python record_demo.py --tts-only regenerate audio, print timings
        .venv/Scripts/python record_demo.py --dry      fast click-through, screenshot per beat
"""
import asyncio, base64, json, math, pathlib, shutil, subprocess, sys, time

import edge_tts
from playwright.async_api import async_playwright

HERE = pathlib.Path(__file__).resolve().parent
HTML = HERE.parent / "carepilot_ai_prototype_v7.2.html"
INTRO = HERE / "intro.html"
WORK = HERE / "build"
OUT = HERE / "carepilot_v7.2_demo.mp4"
VOICE = "en-IN-NeerjaNeural"
RATE = "+6%"
VIEW_W, VIEW_H, DSF = 1440, 810, 4 / 3          # renders at 1920x1080
GAP = 0.3                                         # silence between beats (s)
DRY = "--dry" in sys.argv

INTRO_TEXT = ("CarePilot AI keeps breast cancer survivors, their caregivers and the care team connected after "
              "active treatment. It turns each survivor's care plan into tracked actions, engages patients and "
              "caregivers on WhatsApp in their own language, and lets an AI agent handle routine follow-up "
              "coordination, while coordinators handle exceptions and oncologists keep every clinical decision.")
INTRO_CUES = ["turns", "engages", "lets", "while"]   # words that switch to scenes 1..4

# ---------------------------------------------------------------- cursor overlay
CURSOR_JS = r"""
(() => {
  if (window.__cursorInstalled) return; window.__cursorInstalled = true;
  const install = () => {
    const c = document.createElement('div');
    c.id = '__cursor';
    c.innerHTML = '<svg width="28" height="28" viewBox="0 0 24 24"><path d="M4 2 L4 20 L9 15.5 L12.5 22.5 L15.5 21 L12 14.5 L19 14.5 Z" fill="#111" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    Object.assign(c.style, {position:'fixed', left:'0px', top:'0px', zIndex:2147483647, pointerEvents:'none',
      transform:'translate(-3px,-2px)', filter:'drop-shadow(0 2px 3px rgba(0,0,0,.35))', transition:'none'});
    document.documentElement.appendChild(c);
    const st = document.createElement('style');
    st.textContent = '@keyframes __rip{from{transform:translate(-50%,-50%) scale(.2);opacity:.9}to{transform:translate(-50%,-50%) scale(1);opacity:0}}' +
      '.__ripple{position:fixed;width:46px;height:46px;border-radius:50%;background:rgba(29,79,145,.35);border:2px solid rgba(29,79,145,.9);' +
      'pointer-events:none;z-index:2147483646;animation:__rip .55s ease-out forwards}';
    document.documentElement.appendChild(st);
    window.__mv = (x, y) => { c.style.left = x + 'px'; c.style.top = y + 'px'; };
    window.__ripple = (x, y) => { const r = document.createElement('div'); r.className = '__ripple';
      r.style.left = x + 'px'; r.style.top = y + 'px'; document.documentElement.appendChild(r); setTimeout(() => r.remove(), 700); };
    document.addEventListener('mousemove', e => window.__mv(e.clientX, e.clientY), true);
    document.addEventListener('mousedown', e => window.__ripple(e.clientX, e.clientY), true);
    window.__mv(%X%, %Y%);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install); else install();
})();
"""


class Director:
    def __init__(self, page):
        self.page = page
        self.x, self.y = VIEW_W * 0.55, VIEW_H * 0.45
        self.cdp = None

    async def pause(self, s):
        await self.page.wait_for_timeout(int(s * (80 if DRY else 1000)))

    async def move(self, x, y, dur=None):
        dist = math.hypot(x - self.x, y - self.y)
        dur = dur if dur is not None else min(0.95, max(0.35, dist / 1400))
        steps = 2 if DRY else max(8, int(dur * 60))
        x0, y0 = self.x, self.y
        for i in range(1, steps + 1):
            t = i / steps
            e = t * t * (3 - 2 * t)                      # smoothstep ease
            await self.page.mouse.move(x0 + (x - x0) * e, y0 + (y - y0) * e)
            if not DRY:
                await self.page.wait_for_timeout(int(dur * 1000 / steps))
        self.x, self.y = x, y

    async def settle(self):
        """Wait until any smooth scrolling has finished."""
        last, same = None, 0
        for _ in range(80):
            y = await self.page.evaluate("[scrollY, (document.querySelector('.modal-backdrop')||{}).scrollTop||0].join()")
            same = same + 1 if y == last else 0
            if same >= 2:
                return
            last = y
            await self.page.wait_for_timeout(90)

    async def _center(self, loc):
        await self.settle()
        await loc.scroll_into_view_if_needed(timeout=5000)
        await self.settle()
        b = await loc.bounding_box()
        return b["x"] + b["width"] / 2, b["y"] + b["height"] / 2

    async def hover(self, loc, hold=0.6):
        x, y = await self._center(loc)
        await self.move(x, y)
        await self.pause(hold)

    async def click(self, loc, before=0.25, after=0.6):
        x, y = await self._center(loc)
        await self.move(x, y)
        await self.pause(before)
        await self.page.mouse.click(x, y)
        await self.pause(after)

    async def select(self, loc, value, after=0.8):
        """Native <select> popups are not captured, so show the click then set the value."""
        x, y = await self._center(loc)
        await self.move(x, y)
        await self.pause(0.25)
        await self.page.evaluate("([x,y]) => window.__ripple(x,y)", [x, y])
        await self.pause(0.35)
        await loc.select_option(value)
        await self.pause(after)

    async def scroll(self, dy, dur=1.2, at=None):
        if at:
            await self.move(*at)
        steps = 4 if DRY else max(10, int(dur * 40))
        for _ in range(steps):
            await self.page.mouse.wheel(0, dy / steps)
            await self.page.wait_for_timeout(10 if DRY else int(dur * 1000 / steps))
        await self.pause(0.2)

    async def scroll_to(self, loc, block="start"):
        await loc.evaluate(f"e => e.scrollIntoView({{behavior:'smooth', block:'{block}'}})")
        await self.pause(0.9)
        await self.settle()

    async def top(self):
        await self.page.evaluate("window.scrollTo({top:0, behavior:'smooth'})")
        await self.pause(0.7)


# ---------------------------------------------------------------- the story
def beats():
    """(narration, async action(d, page)) pairs, in order. Beat 0 is the animated intro."""

    def btn(p, name, exact=False):
        return p.get_by_role("button", name=name, exact=exact).first

    def modal(p):
        return p.locator("#modalRoot")

    def row(p, name):
        return p.locator(".v4-row", has_text=name).first

    async def a_intro(d, p):
        """Scenes switch on the cue words; timings come from the TTS word boundaries."""
        t0 = time.time()
        await p.evaluate("scene(0)")
        for n, at in enumerate(d.intro_cues, start=1):
            wait = at - (time.time() - t0) - 0.15
            if wait > 0:
                await d.pause(wait)
            await p.evaluate(f"scene({n})")
        rest = d.intro_len - (time.time() - t0) + 0.4
        if rest > 0:
            await d.pause(rest)
        await p.evaluate("finish()")
        await d.pause(1.0)
        await p.goto(HTML.as_uri())
        await p.wait_for_load_state("networkidle")
        await p.evaluate("document.fonts.ready")
        if d.cdp:  # make sure capture continues on the new document
            await d.cdp.send("Page.stopScreencast")
            await d.cdp.send("Page.startScreencast", SCREENCAST)
        await p.mouse.move(d.x, d.y)
        await d.pause(0.6)

    async def a_ws(d, p):
        await d.hover(p.locator(".screen-title"), 1.0)
        await d.hover(p.locator(".screen-sub"), 1.2)
        await d.hover(p.locator(".v4-guard").first, 1.5)

    async def a_glance(d, p):
        figs = p.locator(".bfig")
        await d.scroll_to(p.locator(".agent-card").first, "center")
        for i in range(4):
            await d.hover(figs.nth(i), 1.2)

    async def a_feed(d, p):
        await d.click(p.locator("#agentdone summary"), after=0.8)
        await d.scroll(300, dur=1.4, at=(VIEW_W * 0.5, VIEW_H * 0.55))
        await d.pause(1.2)

    async def a_hema(d, p):
        await d.click(p.locator(".navtab", has_text="Workspace"), after=0.8)
        item = p.locator(".v4-item", has_text="Hema Shetty").first
        await d.scroll_to(item, "center")
        await d.hover(item.locator(".d"), 2.0)
        await d.click(item.get_by_role("button", name="Approve"), after=1.4)

    async def a_fathima(d, p):
        item = p.locator(".v4-item", has_text="Fathima Begum").first
        await d.hover(item.locator(".tag"), 1.0)
        await d.hover(item.locator(".d"), 1.8)
        await d.click(item.get_by_role("button", name="Approve"), after=1.2)

    async def a_meena(d, p):
        r = row(p, "Meena Rao")
        await d.scroll_to(r, "center")
        await d.hover(r.locator(".v4-reason"), 1.0)
        await d.click(r.get_by_role("button", name="Review handover"), after=1.5)
        await d.hover(modal(p).locator(".msgprev"), 2.0)

    async def a_meena2(d, p):
        await d.click(btn(p, "Send to the doctor as a concern"), after=1.2)

    async def a_lakshmi(d, p):
        r = row(p, "Lakshmi Prakash")
        await d.scroll_to(r, "center")
        await d.hover(r.locator(".v4-reason"), 1.5)
        await d.click(r.locator(".v4-name a"), after=1.0)

    async def a_view(d, p):
        await d.scroll_to(p.locator(".need-agent"), "center")
        await d.hover(p.locator(".card-title", has_text="For you"), 1.0)
        await d.hover(p.locator(".need-agent"), 1.5)
        await d.hover(p.get_by_text("Switch agent off for this patient"), 1.2)

    async def a_cards(d, p):
        await d.scroll(480, dur=1.6, at=(VIEW_W * 0.5, VIEW_H * 0.55))
        await d.pause(1.2)

    async def a_send(d, p):
        await d.top()
        await d.click(btn(p, "Send check-in in Kannada"), after=1.2)
        await d.click(btn(p, "Send in Kannada"), after=1.0)
        await d.click(btn(p, "ಕನ್ನಡ", exact=True), after=1.2)
        await d.click(btn(p, "ಕನ್ನಡ + English"), after=0.6)

    async def a_reply(d, p):
        await d.click(btn(p, "Simulate her reply — free text (demo)"), after=1.0)
        for i in range(3):
            await d.hover(modal(p).locator(".v31-struct select").nth(i), 0.9)

    async def a_confirm(d, p):
        await d.click(btn(p, "Confirm", exact=True), after=1.8)
        await d.hover(modal(p).locator(".v31-panel"), 0.8)

    async def a_rebook(d, p):
        await d.click(btn(p, "Rebook the missed visits"), after=1.0)
        await d.hover(p.locator("#rbD"), 0.6)
        await d.click(btn(p, "Save bookings"), after=1.2)

    async def a_intake(d, p):
        await d.click(p.locator(".navtab", has_text="Intake & verification"), after=1.0)
        r = p.locator("tr", has_text="prescription, Hassan").first
        await d.hover(r, 2.0)
        await d.click(r.get_by_role("button", name="Verify"), after=1.2)

    async def a_verify(d, p):
        await d.click(p.locator("#vc1"), after=0.5)
        await d.click(p.locator("#vc2"), after=0.5)
        await d.click(btn(p, "Verify and link"), after=1.2)

    async def a_onco(d, p):
        await d.select(p.locator("#roleSelect"), "oncologist", after=1.0)
        await d.hover(p.locator(".v4-guard").first, 1.2)

    async def a_concern(d, p):
        card = p.locator(".card", has_text="Lakshmi Prakash").filter(has_text="Patient said").first
        await d.scroll_to(card, "center")
        await d.hover(card.locator(".fb"), 1.6)
        await d.click(card.get_by_role("button", name="Urgent — call today"), after=1.4)

    async def a_flag(d, p):
        flag = p.locator(".v4-flag", has_text="Lakshmi Prakash").first
        await d.scroll_to(flag, "center")
        await d.hover(flag, 1.2)
        await d.click(flag.get_by_role("button", name="Seen — I'll address it in the record"), after=1.0)

    async def a_emr(d, p):
        item = p.locator(".v4-item", has_text="Lakshmi Prakash").filter(has_text="Instruction").first
        await d.click(item.get_by_role("button", name="Demo: instruction appears in the EMR"), after=0.8)
        await d.hover(p.locator("#inT"), 0.8)
        await d.click(modal(p).locator(".btn.primary"), after=1.2)

    async def a_autonomy(d, p):
        await d.select(p.locator("#roleSelect"), "coordinator", after=0.5)
        await d.click(p.locator(".navtab", has_text="Care Plan"), after=0.6)
        await d.click(p.locator(".seg button", has_text="Agent autonomy"), after=1.0)
        await d.hover(p.get_by_text("Stop-and-hand-over rule"), 1.2)
        tbl = p.locator(".card", has_text="Agent does it, logged").first
        await d.scroll_to(tbl, "start")
        await d.hover(tbl.get_by_text("Shadow — proposes, you approve"), 1.2)

    async def a_autonomy2(d, p):
        cc = p.locator(".card", has_text="Coordinator only").last
        await d.scroll_to(cc, "center")
        await d.hover(cc, 1.5)

    async def a_audit(d, p):
        await d.top()
        await d.click(p.locator(".navtab", has_text="Audit trail"), after=1.0)
        await d.click(p.locator(".seg button", has_text="Agent ("), after=1.5)
        await d.scroll(300, dur=1.2, at=(VIEW_W * 0.5, VIEW_H * 0.6))
        await d.top()
        await d.click(p.locator(".seg button", has_text="Human ("), after=1.2)

    async def a_chief(d, p):
        await d.select(p.locator("#roleSelect"), "chief", after=1.0)
        await d.hover(p.locator(".card").first, 1.8)
        await d.hover(p.locator(".card", has_text="Coordination done by the agent").last, 1.8)
        await d.hover(p.locator(".card", has_text="Coordinator hours").last, 1.2)

    async def a_close(d, p):
        await d.move(VIEW_W * 0.5, VIEW_H * 0.3)
        await d.pause(1.0)

    return [
        (INTRO_TEXT, a_intro),
        ("Let's see it in action. We start as Priya Nair, the care coordinator, on her Workspace. "
         "The agent runs the repetitive coordination around the clock, so Priya sees only what needs a person.", a_ws),
        ("Today at a glance: the agent has already taken seven actions since last evening, one patient is fully "
         "handled by the agent, two approvals are waiting, and five patients need Priya.", a_glance),
        ("Every agent action is listed — department lists, refill reminders, report chases — and each one is on "
         "the patient's audit trail.", a_feed),
        ("Let's start with our demo patient, Lakshmi Prakash. She missed three visits and left two messages "
         "unanswered, so the agent stopped chasing and escalated her to Priya.", a_lakshmi),
        ("Her patient view splits the work: what's for Priya, and what the agent is handling, like her refill "
         "reminder. The agent can be switched off for any patient.", a_view),
        ("Below, her follow-up plan runs as order cards, each with a named owner, a cover, and a deadline.", a_cards),
        ("Priya sends the approved monthly check-in in Kannada, to Lakshmi and her husband Prakash, with one-tap "
         "reply buttons, and can read it side by side with English.", a_send),
        ("Lakshmi replies in free text. AI structuring lays out what she said, without interpreting it: arm "
         "swelling for two weeks, tablets taken daily, and a doctor seen in Hassan.", a_reply),
        ("Nothing happens until Priya confirms. Then the concern goes to the oncologist, a care-elsewhere episode "
         "opens, and Lakshmi is marked re-engaged.", a_confirm),
        ("Priya rebooks the missed visits, and a confirmation goes to the family.", a_rebook),
        ("Lakshmi's photos from Hassan arrive in Intake, already matched by phone number. But nothing from outside "
         "counts as fact until a person verifies it.", a_intake),
        ("Priya checks the name and a second identifier, links the prescription to the episode, and verifies it.", a_verify),
        ("Back on the Workspace, some tasks are approve-first. Hema is travelling for her son's wedding, so the "
         "agent proposes pausing her follow-up until the eighteenth of October. One tap approves it, logged with "
         "Priya's name.", a_hema),
        ("Fathima's booking help is still in shadow mode. The agent has drafted a Kannada message with three slots, "
         "but nothing is sent until Priya approves.", a_fathima),
        ("Now, the key safety rule. Meena replied in Hindi: she has booked, but mentions pain at her operation site. "
         "The agent recorded the booking, then stopped. It never responds to a health matter — it hands the "
         "conversation to Priya.", a_meena),
        ("Priya sends it to the oncologist as a concern.", a_meena2),
        ("Now, the oncologist, Doctor Ananya Raghunathan. Needs my decision shows only what needs her, with no "
         "thresholds and no risk scores.", a_onco),
        ("Lakshmi's concern appears in her own words. The doctor decides: one tap marks it urgent, and a "
         "call-today task goes straight to the coordinator.", a_concern),
        ("The outside prescription lists a medicine that isn't on the hospital's list. CarePilot flags the "
         "difference, and the doctor acknowledges it.", a_flag),
        ("Her instruction is written in the hospital record as usual, and CarePilot turns it into a new order card "
         "with an owner and a due date.", a_emr),
        ("Back as the coordinator, the Care Plan tab shows how the agent is governed. Each task is either live, or "
         "in shadow mode, and is promoted only when coordinators consistently accept its proposals.", a_autonomy),
        ("Health matters, document verification, closing cases and phone calls always stay with people.", a_autonomy2),
        ("The audit trail records every action — by the agent, by people, by the system and by AI — so anyone can "
         "see exactly who did what, and when.", a_audit),
        ("Finally, the chief of hospital sees aggregates only: recall completion against the clinic's own baseline, "
         "and the share of coordination now done by the agent, without extra coordinator hours.", a_chief),
        ("CarePilot AI. The agent handles the routine, coordinators handle the exceptions, and oncologists keep "
         "every clinical decision. Thank you for watching.", a_close),
    ]


SCREENCAST = {"format": "jpeg", "quality": 92, "maxWidth": int(VIEW_W * DSF), "maxHeight": int(VIEW_H * DSF)}


# ---------------------------------------------------------------- helpers
def probe_duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return float(json.loads(out)["format"]["duration"])


async def tts(text, path, words=None):
    comm = edge_tts.Communicate(text, VOICE, rate=RATE, boundary="WordBoundary")
    with open(path, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary" and words is not None:
                words.append((chunk["text"], chunk["offset"] / 1e7))


async def synth(items):
    (WORK / "audio").mkdir(parents=True, exist_ok=True)
    durs, cues = [], []
    for i, (text, _) in enumerate(items):
        f = WORK / "audio" / f"beat_{i:02d}.mp3"
        meta = WORK / "audio" / f"beat_{i:02d}.json"
        key = VOICE + RATE + text
        info = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}
        if not (f.exists() and info.get("key") == key):
            words = []
            await tts(text, f, words)
            info = {"key": key, "words": words}
            meta.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
        durs.append(probe_duration(f))
        if i == 0:
            words, k = info["words"], 0
            for w, t in words:
                if k < len(INTRO_CUES) and w.strip(",.").lower() == INTRO_CUES[k]:
                    cues.append(t); k += 1
            assert len(cues) == len(INTRO_CUES), f"intro cue words not found: {cues}"
    return durs, cues


async def record(items, durs, cues):
    frames_dir = WORK / "frames"
    shutil.rmtree(frames_dir, ignore_errors=True)
    frames_dir.mkdir(parents=True)
    shots = WORK / "dry"
    if DRY:
        shutil.rmtree(shots, ignore_errors=True); shots.mkdir(parents=True)
    frames, starts = [], []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        ctx = await browser.new_context(viewport={"width": VIEW_W, "height": VIEW_H}, device_scale_factor=DSF,
                                        color_scheme="light")
        page = await ctx.new_page()
        d = Director(page)
        d.intro_cues, d.intro_len = cues, durs[0]
        await page.add_init_script(CURSOR_JS.replace("%X%", str(d.x)).replace("%Y%", str(d.y)))
        await page.goto(INTRO.as_uri())
        await page.wait_for_load_state("networkidle")
        await page.evaluate("document.fonts.ready")
        await page.mouse.move(d.x, d.y)

        if not DRY:
            d.cdp = await ctx.new_cdp_session(page)

            def on_frame(ev):
                frames.append((ev["metadata"]["timestamp"], ev["data"]))
                asyncio.ensure_future(d.cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]}))

            d.cdp.on("Page.screencastFrame", on_frame)
            await d.cdp.send("Page.startScreencast", SCREENCAST)
            await d.pause(0.8)
        t_video0 = time.time()

        for i, ((text, act), dur) in enumerate(zip(items, durs)):
            t0 = time.time()
            starts.append(t0)
            print(f"[{t0 - t_video0:6.1f}s] beat {i:02d} ({dur:4.1f}s): {text[:60]}...", flush=True)
            await act(d, page)
            if DRY:
                await page.screenshot(path=str(shots / f"beat_{i:02d}.png"))
                continue
            remaining = (t0 + dur + GAP) - time.time()
            if remaining > 0:
                await d.pause(remaining)
            else:
                print(f"    ! actions ran {-remaining:.1f}s past narration", flush=True)
        await d.pause(1.5)
        t_end = time.time()
        if d.cdp:
            await d.cdp.send("Page.stopScreencast")
        await browser.close()
    if DRY:
        return None, None

    # write frames + concat list with real durations
    frames.sort(key=lambda f: f[0])
    kept = [(ts, data) for ts, data in frames if ts <= t_end]
    lines = ["ffconcat version 1.0"]
    for idx, (ts, data) in enumerate(kept):
        p = frames_dir / f"f{idx:06d}.jpg"
        p.write_bytes(base64.b64decode(data))
        nxt = kept[idx + 1][0] if idx + 1 < len(kept) else t_end
        if nxt <= t_video0:
            continue
        lines.append(f"file '{p.name}'")
        lines.append(f"duration {max(0.0, nxt - max(ts, t_video0)):.4f}")
    lines.append(f"file 'f{len(kept) - 1:06d}.jpg'")
    (frames_dir / "list.ffconcat").write_text("\n".join(lines), encoding="utf-8")
    return [s - t_video0 for s in starts], t_end - t_video0


def assemble(offsets, total, n):
    inputs, filters = [], []
    for i in range(n):
        inputs += ["-i", str(WORK / "audio" / f"beat_{i:02d}.mp3")]
        ms = int(offsets[i] * 1000)
        filters.append(f"[{i + 1}:a]adelay={ms}|{ms},aresample=48000[a{i}]")
    mix = "".join(f"[a{i}]" for i in range(n)) + f"amix=inputs={n}:normalize=0:dropout_transition=0,apad[aud]"
    fade_out = max(0, total - 1.2)
    vf = (f"[0:v]fps=30,scale=1920:1080:flags=lanczos,format=yuv420p,"
          f"fade=t=in:st=0:d=0.8,fade=t=out:st={fade_out:.2f}:d=1.2[v]")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-f", "concat", "-safe", "0", "-i", str(WORK / "frames" / "list.ffconcat"), *inputs,
           "-filter_complex", ";".join(filters + [mix, vf]),
           "-map", "[v]", "-map", "[aud]", "-t", f"{total:.2f}",
           "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-tune", "stillimage",
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(OUT)]
    subprocess.run(cmd, check=True)


async def main():
    items = beats()
    durs, cues = await synth(items)
    print(f"{len(items)} beats; intro {durs[0]:.1f}s (cues at {', '.join(f'{c:.1f}' for c in cues)}); "
          f"narration total {sum(durs):.1f}s (+gaps = {sum(durs) + GAP * len(durs):.1f}s)")
    if "--tts-only" in sys.argv:
        return
    offsets, total = await record(items, durs, cues)
    if DRY:
        print(f"dry run ok — screenshots in {WORK / 'dry'}")
        return
    print(f"recorded {total:.1f}s, assembling...")
    assemble(offsets, total, len(items))
    shutil.rmtree(WORK / "frames", ignore_errors=True)
    print(f"done: {OUT}  ({probe_duration(OUT):.1f}s)")


if __name__ == "__main__":
    asyncio.run(main())
