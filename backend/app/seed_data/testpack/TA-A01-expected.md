# TA-A01 · Tamil · Amber (sample for review)

Synthetic test data. Audio: `audio/TA-A01.wav` (about 22 s, 16 kHz mono), made by `scripts/voice_testpack.py`.

| Field | Expected |
|---|---|
| Scenario | Amber 1: left arm swollen, bangles tight |
| Voice | Woman, about 62. Madurai dialect and slang; tearful, trembling voice with stammers; slow pace (0.85); pitch lowered (0.92); background: low TV hum and a pressure-cooker whistle (4–5.6 s) |
| Script (Tamil, Madurai) | ஆத்தாடி… அ-அய்யோ… ரெண்டு நாளா இந்த எடது கை இம்புட்டு வீங்கிப் போயி கெடக்குய்யா… வளையல்லாம் இறுகிப் போச்சு, கழட்டவே ஏலலய்யா… ல-லேசா வலிக்குதுய்யா… எனக்கு அம்புட்டு பயமா இருக்கு டாக்டரம்மா, என்னத்த பண்றதுன்னே தெரியலயே… |
| Meaning | "Oh mother… oh… for two days this left arm has swollen up this much… the bangles have all gone tight, I just can't take them off… it hurts a little… I'm so scared, doctor-amma, I don't know what to do…" |
| Madurai markers | ஆத்தாடி (exclamation), இம்புட்டு / அம்புட்டு (this much / so much), கெடக்கு (is lying, has become), ஏலல (can't), the -ய்யா endings |
| Expected AI tags | health_related YES; symptoms `arm_swelling_or_redness`, `arm_pain`; severity word none |
| Planned questions (10) | Q_SLURRED, Q_AWAKE, Q_CHEST, Q_ARM_RED, Q_TEMP, Q_BANGLES, Q_ARM_SCORE, Q_ARM_SUDDEN, Q_SWOLLEN, Q_SKIN |
| Call answer script | No · No · No · No · 98.6 · Yes · 4 · No · Yes · No |
| Expected tier | Amber: "Today: bangles tighter" (baseline 10 Oct: loose) and "She says the left arm looks swollen or feels heavy" |
| Patient message | Tamil WhatsApp: a doctor will call within 2 hours, with the emergency line |
| Workflow | Pending with Care coordinator (review) → Confirm → Doctor on Duty (call within 2 hours of triage) → picked up → order → draft summary → Submit and close |
| Pass rule | Transcript/translation captures arm or hand + swelling + bangles; never the standard reply; Amber. A fallback to the fixed 12 is a pass (it includes Q_BANGLES). An extra "chest or breathing" tag is a pass, noted |

## Run 2 (3 Nov, Madurai version, live Sarvam)

| Step | Result |
|---|---|
| Transcript (saaras:v3) | ஆத்தாடி ஆ ஐயோ ரெண்டு நாளா இந்த இடது கை இம்புட்டு வீங்கிப் போயி கிடக்குயா வலையெல்லாம் இருகிப் போச்சு கழட்டவே இயலலையால லேசா வலிக்குதுயா எனக்கு அம்புட்டு பயமா இருக்கு டாக்டர் அம்மா என்னத்த பண்றதுன்னே தெரியலையே — dialect words kept (இம்புட்டு, அம்புட்டு); **வளையல்லாம் (bangles) heard as வலையெல்லாம்** |
| Translation | "…this left hand has been swollen and swollen, and the veins have become tight. I can't even remove it; it hurts a little. I'm so scared…" — **bangles became "veins"** |
| Tags (sarvam-105b) | `arm_swelling_or_redness`, `arm_pain` — pass (as expected) |
| Plan | 10 questions, as expected; no floor |
| Tier (with the answer script) | Amber — pass. Finding: the word "bangles" was lost in the Madurai version, but Q_BANGLES is asked anyway (from her record), so the call still checks it |

Run 1 (plain Tamil): tags `arm_swelling_or_redness` + `other_symptom` (no `arm_pain`), 8 questions; still Amber.
