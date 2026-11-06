"""Voice test pack (3 Nov): synthetic patient voice notes in Tamil, Hindi and Telugu for testing what the AI hears
and the Red / Amber / Green workflow under hard audio. Each clip: Sarvam Bulbul speech from a script, then ffmpeg
effects (pace, low pitch, feeble, trembling "crying" voice, background noise). Synthetic test data only.
The clips (scripts, voices, expected tags and answer scripts) are in voice_testpack_clips.py.

    python scripts/voice_testpack.py make TA-A01 HI-R02     # make these clips (Sarvam text-to-speech + ffmpeg)
    python scripts/voice_testpack.py make all [--skip-existing]
    python scripts/voice_testpack.py expect                 # offline: plan, questions, tier and reasons per clip
    python scripts/voice_testpack.py hear all               # live Sarvam: what the app would hear in each clip
    python scripts/voice_testpack.py sheet                  # write testpack/clips.json and testpack/README.md

SARVAM_API_KEY comes from the environment or the git-ignored .env (never printed). Needs ffmpeg on PATH.
"""
import base64
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import voice_testpack_clips as CL  # noqa: E402

PACK = ROOT / "backend" / "app" / "seed_data" / "testpack"
OUT = PACK / "audio"
HEARD = PACK / "heard.json"  # live hearing results, kept between runs
LANG_CODES = {"Tamil": "ta-IN", "Hindi": "hi-IN", "Telugu": "te-IN"}
RATE = 16_000  # phone quality, mono
TODAY = date(2026, 11, 3)
CLIPS = CL.clips()
TAG_SYSTEM = "You tag a patient's message from fixed lists. You never give advice and never judge how serious it is."


def sarvam_key() -> str:
    key = os.environ.get("SARVAM_API_KEY", "")
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            if k.strip() == "SARVAM_API_KEY":
                key = v.strip().strip('"').strip("'")
    if not key:
        sys.exit("No SARVAM_API_KEY (environment or .env)")
    return key


# ---------------------------------------------------------------------------------------------------- making audio
def speak(text: str, language: str, speaker: str, pace: float) -> bytes:
    r = httpx.post("https://api.sarvam.ai/text-to-speech", timeout=60,
                   headers={"api-subscription-key": sarvam_key(), "Content-Type": "application/json"},
                   json={"text": text, "language_code": LANG_CODES[language], "model": "bulbul:v3",
                         "speaker": speaker, "pace": pace, "speech_sample_rate": 22050, "output_audio_codec": "wav"})
    if r.status_code != 200:
        raise RuntimeError(f"Sarvam text-to-speech failed: {r.status_code} {r.text[:200]}")
    return base64.b64decode(r.json()["audios"][0])


def effects(raw: Path, out: Path, st: dict) -> None:
    """The voice chain, then the background (if any) mixed under it, down to 16 kHz mono."""
    voice = []
    pitch = st.get("pitch", 1.0)
    if pitch != 1.0:  # lower the pitch, keep the speed
        voice += [f"asetrate=22050*{pitch}", "aresample=22050", f"atempo={1 / pitch:.4f}"]
    if st.get("tremble"):  # a shaky, tearful voice
        voice += ["vibrato=f=6:d=0.35", "tremolo=f=5:d=0.35"]
    if st.get("muffled"):  # feeble: far from the phone, weak
        voice += ["lowpass=f=2200", "highpass=f=250"]
    voice += [f"volume={st.get('volume', 1.0) * (0.45 if st.get('muffled') else 1.0)}",
              "highpass=f=200", "lowpass=f=3600"]  # a phone line
    chain = f"[0:a]{','.join(voice)}[v]"
    noise = st.get("noise")
    lvl = st.get("noise_level", 0.3)
    if not noise:
        graph = chain + ";[v]aresample=16000[out]"
    else:
        beds = {  # synthetic beds: shaped noise, plus a pressure-cooker whistle or horns
            "home": (f"anoisesrc=color=pink:amplitude={0.12 * lvl}:d=60,lowpass=f=1800,"
                     f"volume=1[n0];sine=f=2650:d=60,volume='{0.18 * lvl}*between(t,4,5.6)':eval=frame,"
                     "tremolo=f=9:d=0.6[n1];[n0][n1]amix=inputs=2:normalize=0[bg]"),
            "street": (f"anoisesrc=color=brown:amplitude={0.25 * lvl}:d=60[n0];sine=f=420:d=60,"
                       f"volume='{0.15 * lvl}*(between(t,2,2.5)+between(t,7,7.4))':eval=frame[n1];"
                       "[n0][n1]amix=inputs=2:normalize=0[bg]"),
            "room": (f"anoisesrc=color=pink:amplitude={0.18 * lvl}:d=60,bandpass=f=900:width_type=h:w=1400,"
                     "tremolo=f=3:d=0.5[bg]"),
        }
        graph = (f"{chain};{beds[noise]};[v][bg]amix=inputs=2:duration=first:normalize=0,"
                 "alimiter=limit=0.95,aresample=16000[out]")
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-i", str(raw), "-filter_complex", graph, "-map", "[out]",
           "-ac", "1", "-ar", str(RATE), "-c:a", "pcm_s16le", str(out)]
    subprocess.run(cmd, check=True)


def make(clip_id: str) -> Path:
    c = CLIPS[clip_id]
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"{clip_id}.wav"
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "raw.wav"
        raw.write_bytes(speak(c["text"], c["language"], c["speaker"], c["style"].get("pace", 1.0)))
        effects(raw, out, c["style"])
    return out


def seconds(p: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                       capture_output=True, text=True, check=True)
    return round(float(r.stdout.strip()), 1)


# ---------------------------------------------------------------------------------------------------- the rules
def histories() -> dict:
    d = json.loads((ROOT / "backend" / "app" / "seed_data" / "triage_history.json").read_text(encoding="utf-8"))
    return {k: v for k, v in d.items() if not k.startswith("_")}


def calm(q) -> str:
    """The answer to a question the clip's script does not cover: calm, no rule fires."""
    return {"Q_TEMP": "98.4", "Q_VOMIT": "0", "Q_LIQUIDS": "YES"}.get(q.id, "2" if q.kind == "number" else "NO")


def run_call(c: dict, tags: dict) -> dict:
    """The call as the app runs it: the router's plan for these tags, her answers question by question (stopping at
    the first danger sign), then the rules."""
    from app import triage_router as TR
    from app import triage_rules as R

    h = histories()[c["mrn"]]
    p = TR.plan(h, {"health_related": tags.get("health_related", "YES"), "symptoms": tags["symptoms"],
                    "severity_word": tags.get("severity_word", "NONE")})
    if p["route"] == "STANDARD_REPLY":
        return {"route": "STANDARD_REPLY", "planned": [], "asked": [], "tier": None, "reasons": []}
    ids, a, asked = p["question_ids"], R.blank(), []
    q = R.next_question(h, a, None, ids)
    while q is not None:
        said = c["answers"].get(q.id) or calm(q)
        R.store(a, q, R.check(q.kind, R.parse(q.kind, said)))
        asked.append(q.id)
        if R.red_codes(a):
            break
        q = R.next_question(h, a, q.id, ids)
    r = R.triage(a, h, TODAY, floors=p["floors"], profile=p["profile"])
    return {"route": "TRIAGE", "profile": p["profile"], "planned": ids, "asked": asked, "floors": p["floors"],
            "tier": {"TIER_1_RED": "Red", "TIER_2_AMBER": "Amber", "TIER_3_GREEN": "Green"}[r["tier"]],
            "red_kind": r.get("red_kind"), "reasons": r["reason_codes"], "why": R.why_lines(r, a, h)}


def expect() -> dict:
    out, bad = {}, []
    for cid, c in CLIPS.items():
        e = run_call(c, c["tags"])
        out[cid] = e
        if e["tier"] != c["tier"]:
            bad.append(f"{cid}: expected {c['tier']}, the rules give {e['tier']} ({e['reasons']})")
    if bad:
        sys.exit("Answer scripts that do not reach their tier:\n" + "\n".join(bad))
    return out


# ---------------------------------------------------------------------------------------------------- hearing
def hear(clip_id: str) -> dict:
    """What the app would hear: Sarvam speech-to-text (transcript and English), the tagger, the arm-word rule."""
    os.environ["SARVAM_API_KEY"] = sarvam_key()
    from app import ai
    from app import triage_router as TR

    c = CLIPS[clip_id]
    p = ai.Sarvam(30, "saaras:v3", "sarvam-105b")
    audio = (OUT / f"{clip_id}.wav").read_bytes()
    ta = p.speech(audio, "audio/wav", c["language"], "transcribe")
    en = p.speech(audio, "audio/wav", c["language"], "translate")
    reply = p.chat(TAG_SYSTEM, f"{TR.TAG_PROMPT}\n\nMessage: {ta}\n(English translation: {en})")
    raw = TR.arm_from_words(TR.parse_tags(reply), ta, en)
    tags = TR.route(raw)["tags"]
    return {"transcript": ta, "translation": en, "tags": {k: tags.get(k) for k in ("health_related", "symptoms",
                                                                                    "severity_word", "valid")}}


def verdict(c: dict, exp: dict, got: dict) -> tuple[str, str]:
    """pass: the same tier, every expected question asked. safe: the same tier, but questions differ (or a fallback).
    fail: the standard reply, or a lower tier than expected."""
    rank = {None: -1, "Green": 0, "Amber": 1, "Red": 2}
    if got["route"] == "STANDARD_REPLY":
        return "fail", "sent the 'not health' standard reply"
    missing = [q for q in exp["asked"] if q not in got["planned"]]
    if rank[got["tier"]] < rank[c["tier"]]:
        return "fail", f"{got['tier']} instead of {c['tier']}" + (f"; not asked: {', '.join(missing)}" if missing else "")
    if rank[got["tier"]] > rank[c["tier"]]:
        return "safe", f"{got['tier']} (higher than {c['tier']})"
    if missing or got["planned"] != exp["planned"]:
        extra = [q for q in got["planned"] if q not in exp["planned"]]
        return "safe", "; ".join(x for x in (f"not asked: {', '.join(missing)}" if missing else "",
                                             f"also asked: {', '.join(extra)}" if extra else "") if x)
    return "pass", ""


# ---------------------------------------------------------------------------------------------------- the sheet
def sheet(exp: dict) -> None:
    heard = json.loads(HEARD.read_text(encoding="utf-8")) if HEARD.exists() else {}
    rows, out = [], {}
    for cid, c in CLIPS.items():
        e, h = exp[cid], heard.get(cid)
        rec = {k: c[k] for k in ("language", "mrn", "tier", "story", "story_text", "dialect", "caller", "speaker",
                                 "style", "text", "meaning", "tags", "answers")}
        rec.update(file=f"audio/{cid}.wav", expected=e)
        wav = OUT / f"{cid}.wav"
        rec["seconds"] = seconds(wav) if wav.exists() else None
        if h:
            got = run_call(c, h["tags"]) if h["tags"].get("valid", True) and h["tags"]["symptoms"] else \
                run_call(c, {**h["tags"], "symptoms": []})
            v, note = verdict(c, e, got)
            rec["heard"] = {**h, "result": got, "verdict": v, "note": note}
        out[cid] = rec
        st = c["style"]
        voice = ", ".join(x for x in (
            f"pace {st['pace']}" if st.get("pace", 1) != 1 else "", f"low pitch {st['pitch']}" if st.get("pitch", 1) != 1
            else "", "feeble" if st.get("muffled") else "", "crying / stammer" if st.get("tremble") else "",
            f"{st['noise']} noise" if st.get("noise") else "") if x) or "clear"
        hv = rec.get("heard")
        rows.append(f"| {cid} | {c['tier']} | {c['story_text']} | {c['dialect']}{' · ' + c['caller'] if c['caller'] != 'patient' else ''} "
                    f"| {voice} | {', '.join(c['tags']['symptoms'])}{' · SEVERE' if c['tags']['severity_word'] == 'SEVERE' else ''} "
                    f"| {len(e['asked'])} | {', '.join(e['reasons'])} "
                    f"| {(', '.join(hv['tags']['symptoms'] or ['—']) + (' · SEVERE' if hv['tags'].get('severity_word') == 'SEVERE' else '')) if hv else '—'} "
                    f"| {('**' + hv['verdict'] + '**' + (': ' + hv['note'] if hv['note'] else '')) if hv else 'not heard yet'} |")
    (PACK / "clips.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    counts = {v: sum(1 for r in out.values() if r.get("heard", {}).get("verdict") == v) for v in ("pass", "safe", "fail")}
    md = [
        "# Voice test pack",
        "",
        "Synthetic test data: 48 patient voice notes (Tamil, Hindi, Telugu; 5 Red, 6 Amber, 5 Green each), made by "
        "`scripts/voice_testpack.py` from `scripts/voice_testpack_clips.py` (Sarvam Bulbul v3 + ffmpeg effects). "
        "Full detail per clip (script, meaning, answers, expected plan, what was heard) is in `clips.json`.",
        "",
        "- **Patients:** Tamil = Lakshmi V. (SYN-0142), Hindi = Savitri (SYN-0011), Telugu = Nirmala (SYN-0013).",
        "- **Expected:** the tags the clip should give, the questions the call then asks, and the tier the answer "
        "script reaches through the real rules (checked offline).",
        "- **Heard:** live Sarvam speech-to-text, translation and tagging of the audio, then the same answer script "
        "on the plan that was actually built. **pass:** same tier, every expected question asked. **safe:** same or "
        "higher tier, but the questions differ. **fail:** the 'not health' reply, or a lower tier.",
        "",
        f"Heard so far: {counts['pass']} pass · {counts['safe']} safe · {counts['fail']} fail.",
        "",
        "| Clip | Tier | Story | Dialect | Voice | Expected tags | Qs | Expected reasons | Heard tags | Verdict |",
        "|---|---|---|---|---|---|---|---|---|---|",
        *rows, "",
    ]
    (PACK / "README.md").write_text("\n".join(md), encoding="utf-8")
    print(f"sheet: {len(out)} clips; heard {sum(counts.values())}: {counts}")


if __name__ == "__main__":
    cmd, args = (sys.argv[1:] or ["sheet"])[0], sys.argv[2:]
    skip = "--skip-existing" in args
    ids = [a for a in args if not a.startswith("--")]
    ids = list(CLIPS) if ids == ["all"] else ids
    if cmd == "make":
        todo = [i for i in ids if not (skip and (OUT / f"{i}.wav").exists())]

        def one(i):
            try:
                return f"{i}: {make(i).stat().st_size // 1024} KB"
            except Exception as e:  # noqa: BLE001
                return f"{i}: FAILED {e}"
        with ThreadPoolExecutor(4) as pool:
            for line in pool.map(one, todo):
                print(line, flush=True)
    elif cmd == "expect":
        for cid, e in expect().items():
            print(cid, e["tier"], len(e["asked"]), e["reasons"])
    elif cmd == "hear":
        heard = json.loads(HEARD.read_text(encoding="utf-8")) if HEARD.exists() else {}

        def one(i):
            try:
                return i, hear(i)
            except Exception as e:  # noqa: BLE001
                return i, {"error": str(e)}
        with ThreadPoolExecutor(3) as pool:
            for i, h in pool.map(one, ids):
                if "error" in h:
                    print(f"{i}: FAILED {h['error']}", flush=True)
                    continue
                heard[i] = h
                print(f"{i}: {h['tags']['symptoms']} {h['tags'].get('severity_word')}", flush=True)
                HEARD.write_text(json.dumps(heard, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    elif cmd == "sheet":
        sheet(expect())
    else:
        sys.exit(__doc__)
