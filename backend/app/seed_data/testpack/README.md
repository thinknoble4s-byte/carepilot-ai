# Voice test pack

Synthetic test data: 48 patient voice notes (Tamil, Hindi, Telugu; 5 Red, 6 Amber, 5 Green each), made by `scripts/voice_testpack.py` from `scripts/voice_testpack_clips.py` (Sarvam Bulbul v3 + ffmpeg effects). Full detail per clip (script, meaning, answers, expected plan, what was heard) is in `clips.json`.

- **Patients:** Tamil = Lakshmi V. (SYN-0142), Hindi = Savitri (SYN-0011), Telugu = Nirmala (SYN-0013).
- **Expected:** the tags the clip should give, the questions the call then asks, and the tier the answer script reaches through the real rules (checked offline).
- **Heard:** live Sarvam speech-to-text, translation and tagging of the audio, then the same answer script on the plan that was actually built. **pass:** same tier, every expected question asked. **safe:** same or higher tier, but the questions differ. **fail:** the 'not health' reply, or a lower tier.

Heard so far: 40 pass · 8 safe · 0 fail.

| Clip | Tier | Story | Dialect | Voice | Expected tags | Qs | Expected reasons | Heard tags | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| TA-R01 | Red | Chest pain or breathless | Chennai (Madras Bashai) | pace 1.25, street noise | chest_or_breathing | 3 | RED_CHEST_PAIN | chest_or_breathing, other_symptom · SEVERE | **pass** |
| TA-R02 | Red | So sleepy she cannot keep her eyes open | Kongu (Coimbatore) · son | low pitch 0.9, room noise | sleepiness | 2 | RED_CANNOT_STAY_AWAKE | sleepiness | **pass** |
| TA-R03 | Red | Left arm red and hot | Tirunelveli | pace 0.9, low pitch 0.93, crying / stammer | arm_swelling_or_redness, arm_pain | 4 | RED_ARM_RED_OR_HOT | arm_swelling_or_redness, arm_pain · SEVERE | **pass** |
| TA-R04 | Red | High fever with shivering | Standard | pace 0.9, feeble | fever_chills | 5 | RED_FEVER_100_4F_PLUS | fever_chills | **pass** |
| TA-R05 | Red | Vomiting many times, cannot keep water down | Chennai (Madras Bashai) · son | pace 1.2, home noise | nausea_vomiting | 8 | RED_VOMITING_6_PLUS | nausea_vomiting, tiredness | **safe**: also asked: Q_TIRED_DAYS, Q_TIRED_WORSE |
| TA-A01 | Amber | Arm swollen, bangles tight | Madurai | pace 0.85, low pitch 0.92, crying / stammer, home noise | arm_swelling_or_redness, arm_pain | 10 | AMBER_BANGLES_TIGHTER_THAN_BASELINE, AMBER_ARM_SWOLLEN | arm_swelling_or_redness, arm_pain, other_symptom | **pass** |
| TA-A02 | Amber | Headache worse than last time | Kongu (Coimbatore) | pace 0.95, low pitch 0.88 | headache | 9 | AMBER_HEADACHE_WORSE_THAN_LAST_TIME | headache | **pass** |
| TA-A03 | Amber | Arm pain started suddenly | Chennai (Madras Bashai) | pace 1.2, crying / stammer | arm_pain | 8 | AMBER_ARM_PAIN_SUDDEN_ONSET | arm_pain | **pass** |
| TA-A04 | Amber | Tired for more than a week | Tirunelveli | pace 0.85, low pitch 0.9, feeble | tiredness | 8 | AMBER_TIREDNESS_7_DAYS_PLUS | tiredness | **pass** |
| TA-A05 | Amber | Says 'severe' in her own words | Standard · son | street noise | arm_pain · SEVERE | 8 | AMBER_PATIENT_SAYS_SEVERE | arm_pain, other_symptom · SEVERE | **pass** |
| TA-A06 | Amber | A symptom we have no questions for | Madurai | pace 1.15, home noise | other_symptom | 6 | AMBER_OTHER_SYMPTOM_REPORTED | other_symptom · SEVERE | **pass** |
| TA-G01 | Green | Mild headache | Standard | low pitch 0.88 | headache | 9 |  | headache | **pass** |
| TA-G02 | Green | A bit tired for 2 days | Kongu (Coimbatore) | feeble, room noise | tiredness | 8 |  | tiredness | **pass** |
| TA-G03 | Green | Hand aching a little | Tirunelveli | home noise | arm_pain | 8 |  | arm_pain | **pass** |
| TA-G04 | Green | Nausea after tablets, eating fine | Chennai (Madras Bashai) | pace 1.2 | nausea_vomiting | 10 |  | nausea_vomiting | **pass** |
| TA-G05 | Green | Health worry mixed with daily chatter | Madurai | pace 1.05, room noise | headache, tiredness | 11 |  | headache | **safe**: not asked: Q_TIRED_DAYS, Q_TIRED_WORSE |
| HI-R01 | Red | Chest pain or breathless | Haryanvi | pace 1.2, crying / stammer | chest_or_breathing | 3 | RED_CHEST_PAIN | chest_or_breathing | **pass** |
| HI-R02 | Red | So sleepy she cannot keep her eyes open | Bhojpuri · son | low pitch 0.9, room noise | sleepiness | 2 | RED_CANNOT_STAY_AWAKE | other_symptom | **safe** |
| HI-R03 | Red | Left arm red and hot | Mumbaiya | pace 1.25, street noise | arm_swelling_or_redness, arm_pain | 4 | RED_ARM_RED_OR_HOT | arm_swelling_or_redness, arm_pain | **pass** |
| HI-R04 | Red | High fever with shivering | Standard | pace 0.85, feeble | fever_chills | 5 | RED_FEVER_100_4F_PLUS | fever_chills | **pass** |
| HI-R05 | Red | Vomiting many times, cannot keep water down | Awadhi | low pitch 0.9, home noise | nausea_vomiting | 8 | RED_VOMITING_6_PLUS | nausea_vomiting, other_symptom | **pass** |
| HI-A01 | Amber | Arm swollen, bangles tight | Bhojpuri | pace 0.9, low pitch 0.92, crying / stammer, home noise | arm_swelling_or_redness, arm_pain | 10 | AMBER_BANGLES_TIGHTER_THAN_BASELINE, AMBER_ARM_SWOLLEN | arm_swelling_or_redness, arm_pain | **pass** |
| HI-A02 | Amber | Headache worse than last time | Standard | low pitch 0.88 | headache | 9 | AMBER_HEADACHE_WORSE_THAN_LAST_TIME | headache | **pass** |
| HI-A03 | Amber | Arm pain started suddenly | Haryanvi | crying / stammer | arm_pain | 8 | AMBER_ARM_PAIN_SUDDEN_ONSET | arm_pain | **pass** |
| HI-A04 | Amber | Tired for more than a week | Mumbaiya | pace 0.9, feeble | tiredness | 8 | AMBER_TIREDNESS_7_DAYS_PLUS | tiredness | **pass** |
| HI-A05 | Amber | Says 'severe' in her own words | Standard · son | street noise | arm_pain · SEVERE | 8 | AMBER_PATIENT_SAYS_SEVERE | arm_pain · SEVERE | **pass** |
| HI-A06 | Amber | A symptom we have no questions for | Awadhi | pace 1.15, room noise | other_symptom | 6 | AMBER_OTHER_SYMPTOM_REPORTED | other_symptom | **pass** |
| HI-G01 | Green | Mild headache | Bhojpuri | low pitch 0.9 | headache | 9 |  | headache | **pass** |
| HI-G02 | Green | A bit tired for 2 days | Haryanvi | feeble, room noise | tiredness | 8 |  | tiredness, other_symptom | **safe**: Amber (higher than Green) |
| HI-G03 | Green | Hand aching a little | Mumbaiya | pace 1.1, home noise | arm_pain | 8 |  | arm_pain | **pass** |
| HI-G04 | Green | Nausea after tablets, eating fine | Standard | pace 1.2 | nausea_vomiting | 10 |  | nausea_vomiting | **pass** |
| HI-G05 | Green | Health worry mixed with daily chatter | Awadhi | room noise | headache, tiredness | 11 |  | tiredness, headache | **pass** |
| TE-R01 | Red | Chest pain or breathless | Telangana | pace 1.2, crying / stammer | chest_or_breathing | 3 | RED_CHEST_PAIN | chest_or_breathing, other_symptom | **pass** |
| TE-R02 | Red | So sleepy she cannot keep her eyes open | Coastal Andhra · son | low pitch 0.9, room noise | sleepiness | 2 | RED_CANNOT_STAY_AWAKE | sleepiness | **pass** |
| TE-R03 | Red | Left arm red and hot | Rayalaseema | low pitch 0.93, crying / stammer | arm_swelling_or_redness, arm_pain | 4 | RED_ARM_RED_OR_HOT | arm_swelling_or_redness, other_symptom · SEVERE | **safe** |
| TE-R04 | Red | High fever with shivering | Standard | pace 0.85, feeble | fever_chills | 5 | RED_FEVER_100_4F_PLUS | fever_chills | **pass** |
| TE-R05 | Red | Vomiting many times, cannot keep water down | Hyderabadi · son | pace 1.2, street noise | nausea_vomiting | 8 | RED_VOMITING_6_PLUS | nausea_vomiting | **pass** |
| TE-A01 | Amber | Arm swollen, bangles tight | Coastal Andhra | pace 0.9, low pitch 0.92, crying / stammer, home noise | arm_swelling_or_redness, arm_pain | 10 | AMBER_BANGLES_TIGHTER_THAN_BASELINE, AMBER_ARM_SWOLLEN | arm_swelling_or_redness, arm_pain, other_symptom | **pass** |
| TE-A02 | Amber | Headache worse than last time | Telangana | low pitch 0.88 | headache | 9 | AMBER_HEADACHE_WORSE_THAN_LAST_TIME | headache, fever_chills | **safe**: also asked: Q_CHILLS, Q_FEELS_HOT |
| TE-A03 | Amber | Arm pain started suddenly | Rayalaseema | pace 1.15, crying / stammer | arm_pain | 8 | AMBER_ARM_PAIN_SUDDEN_ONSET | arm_pain | **pass** |
| TE-A04 | Amber | Tired for more than a week | Coastal Andhra | pace 0.85, feeble | tiredness | 8 | AMBER_TIREDNESS_7_DAYS_PLUS | other_symptom | **safe**: not asked: Q_TIRED_DAYS, Q_TIRED_WORSE |
| TE-A05 | Amber | Says 'severe' in her own words | Hyderabadi · son | street noise | arm_pain · SEVERE | 8 | AMBER_PATIENT_SAYS_SEVERE | arm_pain · SEVERE | **pass** |
| TE-A06 | Amber | A symptom we have no questions for | Telangana | pace 1.15, home noise | other_symptom | 6 | AMBER_OTHER_SYMPTOM_REPORTED | other_symptom | **pass** |
| TE-G01 | Green | Mild headache | Standard | low pitch 0.88 | headache | 9 |  | headache | **pass** |
| TE-G02 | Green | A bit tired for 2 days | Rayalaseema | feeble, room noise | tiredness | 8 |  | tiredness | **pass** |
| TE-G03 | Green | Hand aching a little | Telangana | home noise | arm_pain | 8 |  | other_symptom | **safe**: Amber (higher than Green) |
| TE-G04 | Green | Nausea after tablets, eating fine | Coastal Andhra | pace 1.2 | nausea_vomiting | 10 |  | nausea_vomiting | **pass** |
| TE-G05 | Green | Health worry mixed with daily chatter | Hyderabadi | room noise | headache, tiredness | 11 |  | tiredness, headache | **pass** |
