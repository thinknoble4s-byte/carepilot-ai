"""UC1-V demo kit (spec sections 9 and 10.1). Uses the real Sarvam API; the key comes from SARVAM_API_KEY or .env.

    python scripts/voice_escalation_kit.py clips     make missing synthetic patient clips (P1 for each name; each script's own voice): a clean take (the demo
                                                     plays it) and a phone-quality take (tests speech-to-text)
    python scripts/voice_escalation_kit.py lines     record missing agent lines (each name) by sentence, listen back
                                                     to each (again after any wording change); exit 1 lists the
                                                     sentences a person must listen to
    python scripts/voice_escalation_kit.py check     run the section 9 test set on the real models, as typed text and
                                                     as speech, plus both takes of each clip; exit 1 on a wrong row

The clips are stand-ins until a Hindi speaker records them (spec section 11, item 5). No database is used.
"""
import io
import json
import random
import re
import subprocess
import sys
import tempfile
import wave
from array import array
from datetime import date, time
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from local_demo import load_sarvam_key  # noqa: E402

from app import ai, reference, voice_script, voice_sorter  # noqa: E402

LANGUAGE = "Hindi"
AUDIO = ROOT / "backend" / "app" / "seed_data" / "uc1v" / "audio" / LANGUAGE.lower()
STEP = {"L1": "identity", "L2": "booking", "L5": "slot"}
CLIP_EXPECTS = {"P1": ("identity", "YES"), "P2": ("booking", "NO"), "P3": ("booking", "YES"), "P4": ("slot", "YES"),
                "P5": ("booking", "HEALTH_CONCERN")}


def ffmpeg(data: bytes, out: Path, chain: str, *codec: str) -> None:
    codec = codec or ("-ar", "16000", "-c:a", "pcm_s16le")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "in.wav"
        src.write_bytes(data)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), "-filter_complex", chain, "-map", "[out]",
                        "-ac", "1", *codec, str(out)], check=True)


def voice(text: str, speaker: str, style: dict | None = None) -> bytes:
    uc = reference.uc1v()["settings"]
    sarvam = ai.provider_for(uc["ai"], 30)
    return sarvam.tts(text, LANGUAGE, speaker, uc["voice_escalation"]["tts_model"], style)


def make_clips() -> None:
    AUDIO.mkdir(parents=True, exist_ok=True)
    speaker = reference.uc1v()["settings"]["voice_escalation"]["patient_speaker"]
    pack = reference.uc1v_library()["languages"][LANGUAGE]
    scripts = reference.uc1v()["scripts"]
    wanted = [(None, clip_id) for clip_id in pack["clips"]]
    wanted += [(key, clip_id) for key, s in scripts.items() if s.get("patient_voice")
               for clip_id in dict.fromkeys(s["answers"].values())]
    sarvam = ai.provider_for(reference.uc1v()["settings"]["ai"], 30)
    for full_name in pack["names"]:
        for script, clip_id in wanted:
            c = voice_script.clip(LANGUAGE, clip_id, {"full_name": full_name}, script)
            if c["audio"]:
                continue
            if not script:
                raw = voice(c["text"], speaker)
            else:  # a faster, lower voice is easier to mishear: keep the take heard closest to the words
                best = None
                for _ in range(8):
                    take = voice(c["text"], speaker, scripts[script]["patient_voice"])
                    heard = sarvam.speech(take, "audio/wav", LANGUAGE, "transcribe")
                    if not best or differs(c["text"], heard) < best[0]:
                        best = differs(c["text"], heard), take, heard
                    if not best[0]:
                        break
                print(c["take"], f"| off by {best[0]} | heard: {best[2]}")
                raw = best[1]
            ffmpeg(raw, AUDIO / f"{c['take']}.wav", "[0:a]aresample=16000[out]")
            ffmpeg(raw, AUDIO / f"{c['take']}-phone.wav",
                   "[0:a]highpass=f=300,lowpass=f=3400,aresample=8000,aresample=16000,volume=0.8[v];"
                   "anoisesrc=color=pink:amplitude=0.02:d=30:seed=7[n];[v][n]amix=inputs=2:duration=first:normalize=0[out]")
            print("made", c["take"])


# The same spoken words, as the library and as speech-to-text write them.
SPELLING = ((r"WhatsApp", "व्हाट्सएप"), (r"DEXA", "डेक्सा"), (r"\b108\b", "एक सौ आठ"), (r"\b112\b", "एक सौ बारह"),
            (r"\b10[:.]30\b", "साढ़े दस"), (r"\b1 (?=दिसंबर)", "एक "), (r"\b3 (?=महीने)", "तीन "),
            (r"\b2 (?=घंटे)", "दो "))


def spelled(text: str) -> str:
    for pattern, words in SPELLING:
        text = re.sub(pattern, words, text)
    return re.sub(r"(?<!\d)0+(?!\d)", lambda m: " ".join(["शून्य"] * len(m.group())), text)


def differs(written: str, heard: str) -> int:
    """How far a take is from the written words: its longest stretch left out, changed or added."""
    written, heard = spelled(written), spelled(heard)
    a, b = (re.sub(r"[^\w\u0900-\u097f]", "", voice_sorter.plain(t)) for t in (written, heard))
    blocks = SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
    return max((max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in blocks if tag != "equal"), default=0)


def requests_for(text: str) -> list[str]:
    """One request for each sentence. A phone number is said in its groups, so that every digit can be heard back."""
    out = []
    for sentence in re.split(r"(?<=[।?])\s+", text.strip()):
        number = re.search(r"\d{3}-\d{3}-\d{4}", sentence)
        if not number:
            out.append(sentence)
            continue
        out.append(sentence[:number.start()].rstrip(" :") + "।")
        for group in number.group().split("-"):
            out.append(", ".join("शून्य" if d == "0" else d for d in group) + "।")
    return out


def say_whole(text: str, speaker: str) -> tuple[bytes, list[str]]:
    """R1: Bulbul leaves out clauses of a long request, so each sentence is its own request and is listened back."""
    sarvam = ai.provider_for(reference.uc1v()["settings"]["ai"], 30)
    parts, doubts = [], []
    for sentence in requests_for(text):
        takes = []
        for _ in range(8):
            audio = voice(sentence, speaker)
            heard = sarvam.speech(audio, "audio/wav", LANGUAGE, "transcribe")
            takes.append((differs(sentence, heard), audio, heard))
            if takes[-1][0] < 4:
                break
        missed, audio, heard = min(takes, key=lambda t: t[0])
        parts.append(audio)
        if missed >= 4:
            doubts.append(f"written: {sentence}\n      heard:   {heard}")
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        for i, part in enumerate(parts):
            with wave.open(io.BytesIO(part)) as r:
                if i == 0:
                    w.setparams(r.getparams())
                else:
                    w.writeframes(b"\0" * int(r.getframerate() * 0.25) * r.getsampwidth() * r.getnchannels())
                w.writeframes(r.readframes(r.getnframes()))
    return out.getvalue(), doubts


def make_lines() -> int:
    uc = reference.uc1v()
    cfg = uc["settings"]["voice_escalation"]
    slot = next(s for s in uc["slots"] if s["status"] == "free" and s["date"] >= "2026-12-01")  # the demo's slot
    unsure = 0
    when = {"slot_date": date.fromisoformat(slot["date"]), "slot_time": time.fromisoformat(slot["time"])}
    pack = reference.uc1v_library()["languages"][LANGUAGE]
    for full_name in pack["names"]:
        words, _ = voice_script.values(LANGUAGE, {"full_name": full_name}, when)
        for ln in pack["lines"]:
            if ln["channel"] != "voice":
                continue
            text = voice_script.fill(ln["text"], words)
            out = voice_script.agent_audio_path(LANGUAGE, text, cfg["agent_speaker"], cfg["tts_model"])
            if out.exists():
                continue  # the same words are already recorded and checked
            out.parent.mkdir(parents=True, exist_ok=True)
            audio, doubts = say_whole(text, cfg["agent_speaker"])
            ffmpeg(audio, out, "[0:a]anull[out]", "-ar", "22050", "-c:a", "libmp3lame", "-b:a", "64k")
            print("made", ln["line_id"], full_name, out.name)
            for doubt in doubts:
                print("   LISTEN", doubt)
            unsure += len(doubts)
    return 1 if unsure else 0


def quiet_wav(noise: int = 0, seconds: float = 2.0) -> bytes:
    rng, buf = random.Random(7), io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(array("h", (rng.randint(-noise, noise) if noise else 0
                                  for _ in range(int(16000 * seconds)))).tobytes())
    return buf.getvalue()


def check() -> int:
    settings = reference.uc1v()["settings"]
    # The sorter reads only the settings and the demo AI switch, so no database is needed here.
    voice_sorter.policy.settings = lambda _conn: settings
    voice_sorter.ai.sarvam_enabled = lambda _conn: True
    rows = json.loads((ROOT / "tests" / "fixtures" / "uc1v_sorter_set.json").read_text(encoding="utf-8"))
    cases = []
    for r in rows:
        step = STEP[r["step"]]
        if r["row"] == 12:
            cases += [("row 12 silence", step, "UNCLEAR", {"audio": quiet_wav()}),
                      ("row 12 noise", step, "UNCLEAR", {"audio": quiet_wav(3000)})]
            continue
        want = {1: "YES", 2: "UNCLEAR", 3: "NO"}.get(r["row"], r["expected"])
        speaker = "shubh" if r["row"] == 3 else settings["voice_escalation"]["patient_speaker"]
        cases += [(f"row {r['row']} text", step, want, {"text": r["text"]}),
                  (f"row {r['row']} speech", step, want, {"audio": voice(r["text"], speaker)})]
    for clip_id, (step, want) in CLIP_EXPECTS.items():
        for take in ("", "-phone"):
            cases.append((f"{clip_id}{take}", step, want, {"audio": (AUDIO / f"{clip_id}{take}.wav").read_bytes()}))

    names = reference.uc1v_library()["languages"][LANGUAGE]["names"]
    sunita, savitri = names["Sunita Verma"], names["Savitri Patil"]
    sushila = names["Sushila Kulkarni"]
    cases = [(*case, sunita) for case in cases]
    takes = [(f"P1{n['clip']}-{s}", n) for s in ("s2", "s3") for n in (sunita, savitri, sushila)]
    takes += [("P2-s2", savitri), ("P3-s2", savitri), ("P4-s2", savitri), ("P5-s3", savitri), ("P5-s3", sushila)]
    for take, who in takes:
        step, want = CLIP_EXPECTS[take[:2]]
        for kind in ("", "-phone"):
            cases.append((f"{take}{kind}", step, want, {"audio": (AUDIO / f"{take}{kind}.wav").read_bytes()}, who))
    for take in ("", "-phone"):
        cases.append((f"P1-savitri{take}", "identity", "YES",
                      {"audio": (AUDIO / f"P1-savitri{take}.wav").read_bytes()}, savitri))
    cases += [("other name, Savitri", "identity", {"NO", "UNCLEAR"}, {"text": "जी हाँ, मैं सुनीता बोल रही हूँ।"}, savitri),
              ("other name, speech", "identity", {"NO", "UNCLEAR"}, {"audio": (AUDIO / "P1.wav").read_bytes()}, savitri),
              ("other name, Sushila", "identity", {"NO", "UNCLEAR"}, {"audio": (AUDIO / "P1-savitri-s2.wav").read_bytes()}, sushila),
              ("other name, S3", "identity", {"NO", "UNCLEAR"}, {"audio": (AUDIO / "P1-sushila-s3.wav").read_bytes()}, savitri),
              ("other name, Sunita", "identity", {"NO", "UNCLEAR"}, {"audio": (AUDIO / "P1-savitri.wav").read_bytes()}, sunita)]

    wrong = 0
    for name, step, want, said, who in cases:
        others = tuple(n["नाम"] for n in names.values() if n is not who)
        got = voice_sorter.sort_reply(None, step=step, language=LANGUAGE, name=who, other_names=others, **said)
        if isinstance(want, set):
            want = got["label"] if got["label"] in want else " or ".join(sorted(want))
        ok = got["label"] == want
        wrong += not ok
        note = f"{got['reason']} | heard: {got['transcript']}" + (f" | {got['errors']}" if got["errors"] else "")
        print(f"{'ok  ' if ok else 'MISS'} {name:18} want {want:15} got {got['label']:15} {note}")
    print(f"{len(cases) - wrong} of {len(cases)} as expected ({got['model']})")
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if not load_sarvam_key():
        sys.exit("No SARVAM_API_KEY (environment or .env)")
    if sys.argv[1:] == ["clips"]:
        make_clips()
    elif sys.argv[1:] == ["lines"]:
        sys.exit(make_lines())
    elif sys.argv[1:] == ["check"]:
        sys.exit(check())
    else:
        sys.exit(__doc__)
