"""Run the pre-demo test pack (tests/CarePilot_PreDemo_Test_Pack_v0.1.docx) through the real reply sorter on the
real Sarvam models, and save every run to one results file. No database is used.

    python scripts/predemo_pack.py run [--plan short|full] [--workers 4]   -> tests/predemo_pack/results.json
    python scripts/predemo_pack.py report                                    -> documents/CarePilot_Sorter_Test_Results.html

Plans (pack section 5): full = rounds A, B and C for all 80 lines, three times each (720 runs);
short = round A for all 80 lines three times, round B for all 80 once, round C for the 40 concern lines once (360).
Part C edge cases run once in round A and are never scored.

Round B and C audio: there are no human recordings yet (pack section 4), so the lines are spoken by Sarvam Bulbul
(the same stand-in voice as the demo clips; N06 in a male voice) and round C adds the phone-quality chain of
scripts/voice_escalation_kit.py. The report says so. The audio is cached in the scratch folder given by --audio.
"""
import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from local_demo import load_sarvam_key  # noqa: E402

from app import ai, reference, voice_sorter as VS  # noqa: E402

PACK = ROOT / "tests" / "fixtures" / "predemo_pack_80.json"
FRESH = ROOT / "tests" / "fixtures" / "predemo_pack_negation.json"  # written after the negation fix
OUT = ROOT / "tests" / "predemo_pack"
STEP = {"L1": "identity", "L2": "booking", "L4": "booking", "L5": "slot"}
LANGUAGE = "Hindi"
PHONE = ("[0:a]highpass=f=300,lowpass=f=3400,aresample=8000,aresample=16000,volume=0.8[v];"
         "anoisesrc=color=pink:amplitude=0.02:d=30:seed=7[n];[v][n]amix=inputs=2:duration=first:normalize=0[out]")


def settings() -> dict:
    return reference.uc1v()["settings"]


def ffmpeg(data: bytes, chain: str) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp) / "in.wav", Path(tmp) / "out.wav"
        src.write_bytes(data)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), "-filter_complex", chain, "-map", "[out]",
                        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out)], check=True)
        return out.read_bytes()


def silence_wav() -> bytes:
    """N27: five seconds with only low background noise."""
    from voice_escalation_kit import quiet_wav

    return quiet_wav(600, 5.0)


def audio_for(line: dict, kind: str, cache: Path) -> bytes:
    key = hashlib.sha1(f"{line['id']}|{line['hindi']}".encode()).hexdigest()[:10]
    path = cache / f"{line['id']}_{key}_{kind}.wav"
    if path.exists():
        return path.read_bytes()
    if line["id"] == "N27":
        raw = silence_wav()
    else:
        s = settings()
        speaker = "shubh" if line["id"] == "N06" else s["voice_escalation"]["patient_speaker"]
        sarvam = ai.provider_for(s["ai"], 30)
        raw = sarvam.tts(line["hindi"], LANGUAGE, speaker, s["voice_escalation"]["tts_model"])
    data = ffmpeg(raw, "[0:a]aresample=16000[out]" if kind == "clean" else PHONE)
    cache.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def trigger(r: dict) -> str | None:
    """The words that made it a safety stop, for the score sheet."""
    if r["reason"] == "health_word":
        m = VS.HEALTH_HI.search(VS.plain(r["transcript"])) or VS.HEALTH_EN.search(VS.plain(r["translation"]))
        return f"word list: “{m.group(0)}”" if m else "word list"
    if r["failed"]:
        return f"AI fault: {VS.FAULT_TEXT.get(r['reason'], r['reason'])}"
    if r["label"] == VS.HEALTH:
        return "the model's label"
    return None


def one(line: dict, round_: str, run: int, cache: Path) -> dict:
    names = reference.uc1v_library()["languages"][LANGUAGE]["names"]
    who = names["Sunita Verma"]
    others = tuple(n["नाम"] for full, n in names.items() if full != "Sunita Verma")
    said = {"text": "" if line["id"] == "N27" else line["hindi"]} if round_ == "A" else \
        {"audio": audio_for(line, "clean" if round_ == "B" else "phone", cache)}
    for attempt in range(1, 4):  # pack section 6: a stop caused by a fault is not a sorter result; run it again
        t0 = time.monotonic()
        r = VS.sort_reply(None, step=STEP[line["step"]], language=LANGUAGE, name=who, other_names=others, **said)
        seconds = round(time.monotonic() - t0, 2)
        if not r["failed"]:
            break
        print(f"  fault on {line['id']} {round_}{run} (attempt {attempt}): {r['errors']}", flush=True)
        if any("429" in err for err in r["errors"]):
            time.sleep(5 * attempt)  # rate limited: wait before running the line again
    return {"id": line["id"], "round": round_, "run": run, "label": r["label"], "reason": r["reason"],
            "stop": r["label"] == VS.HEALTH, "trigger": trigger(r), "transcript": r["transcript"],
            "translation": r["translation"], "fault": r["failed"], "fault_attempts": attempt - 1 if not r["failed"] else attempt,
            "errors": r["errors"], "seconds": seconds}


def run(plan: str, workers: int, cache: Path) -> None:
    s = settings()
    VS.policy.settings = lambda _conn: s  # the sorter reads only the settings and the demo AI switch
    VS.ai.sarvam_enabled = lambda _conn: True
    lines = json.loads(PACK.read_text(encoding="utf-8"))["lines"]
    scored = [ln for ln in lines if ln["part"] in "AB"]
    fresh = json.loads(FRESH.read_text(encoding="utf-8"))["lines"] if FRESH.exists() else []
    lines += fresh
    jobs = [(ln, "A", n) for ln in scored for n in (1, 2, 3)] + [(ln, "A", 1) for ln in lines if ln["part"] == "C"]
    jobs += [(ln, "A", n) for ln in fresh for n in (1, 2, 3)] + [(ln, "B", 1) for ln in fresh]
    if plan == "full":
        jobs += [(ln, rd, n) for rd in "BC" for ln in scored for n in (1, 2, 3)]
    else:
        jobs += [(ln, "B", 1) for ln in scored] + [(ln, "C", 1) for ln in scored if ln["concern"]]
    for ln in {id(j[0]): j[0] for j in jobs if j[1] in "BC"}.values():  # make the audio first, once per line
        for kind in ("clean", "phone") if ln["part"] != "F" else ("clean",):
            audio_for(ln, kind, cache)
    print(f"{len(jobs)} runs, {workers} at a time", flush=True)
    started = datetime.now()
    done = []
    with ThreadPoolExecutor(workers) as pool:
        for n, res in enumerate(pool.map(lambda j: one(*j, cache), jobs), 1):
            done.append(res)
            if n % 20 == 0:
                print(f"  {n}/{len(jobs)}", flush=True)
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "status", "--porcelain", "backend/app"], cwd=ROOT, capture_output=True,
                                text=True).stdout.strip())
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps({
        "frozen": {"date": started.strftime("%d %b %Y %H:%M"), "finished": datetime.now().strftime("%H:%M"),
                   "commit": commit + (" + uncommitted changes in backend/app" if dirty else ""),
                   "speech_model": s["ai"].get("speech_model"), "language_model": s["ai"].get("language_model"),
                   "translation_model": "mayura:v1", "voice_for_audio": f"{s['voice_escalation']['tts_model']} "
                   f"({s['voice_escalation']['patient_speaker']}; N06 shubh)", "prompt_version": VS.PROMPT_VERSION,
                   "plan": plan, "pack": "v0.1 draft, 23 Nov 2026"},
        "lines": lines, "runs": done}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved", OUT / "results.json")


def retry_faults(cache: Path) -> None:
    """Run again, one at a time, the runs that still ended in a technical fault (pack section 6)."""
    s = settings()
    VS.policy.settings = lambda _conn: s
    VS.ai.sarvam_enabled = lambda _conn: True
    path = OUT / "results.json"
    d = json.loads(path.read_text(encoding="utf-8"))
    lines = {ln["id"]: ln for ln in d["lines"]}
    for i, r in enumerate(d["runs"]):
        if r["fault"]:
            new = one(lines[r["id"]], r["round"], r["run"], cache)
            new["fault_attempts"] += r["fault_attempts"]
            d["runs"][i] = new
            print(r["id"], r["round"], r["run"], "->", new["label"], "(fault)" if new["fault"] else "", flush=True)
    path.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser()
    p.add_argument("what", choices=["run", "retry-faults", "report"])
    p.add_argument("--plan", choices=["short", "full"], default="short")
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--audio", default=str(Path(tempfile.gettempdir()) / "carepilot_predemo_audio"))
    a = p.parse_args()
    if a.what == "run":
        if not load_sarvam_key():
            sys.exit("No SARVAM_API_KEY (environment or .env)")
        run(a.plan, a.workers, Path(a.audio))
    elif a.what == "retry-faults":
        load_sarvam_key()
        retry_faults(Path(a.audio))
    else:
        from predemo_report import main as report

        report(OUT / "results.json", ROOT / "documents" / "CarePilot_Sorter_Test_Results.html")
