"""The voice test pack's 48 clips (3 Nov): 16 per language (5 Red, 6 Amber, 5 Green), each with the patient's voice
note, the voice conditions, the expected AI tags and the call's answer script. Synthetic test data only.

Patients: Tamil = Lakshmi V. (SYN-0142), Hindi = Savitri (SYN-0011), Telugu = Nirmala (SYN-0013); all three have a
left-arm baseline (loose bangles), so Q_BANGLES is always asked.
style: pace (Bulbul 0.5-2), pitch (1.0 as spoken; lower is deeper), volume, muffled (feeble), tremble (crying or
tense), noise ("home": TV hum and a pressure-cooker whistle; "street": traffic and horns; "room": a crowd), noise_level.
answers: the call's answer per question (on-screen button values); questions not listed get a calm default.
"""

# The five stories per tier, the same in each language (each language words them its own way)
STORIES = {
    "R1": "Chest pain or breathless", "R2": "So sleepy she cannot keep her eyes open", "R3": "Left arm red and hot",
    "R4": "High fever with shivering", "R5": "Vomiting many times, cannot keep water down",
    "A1": "Arm swollen, bangles tight", "A2": "Headache worse than last time", "A3": "Arm pain started suddenly",
    "A4": "Tired for more than a week", "A5": "Says 'severe' in her own words",
    "A6": "A symptom we have no questions for",
    "G1": "Mild headache", "G2": "A bit tired for 2 days", "G3": "Hand aching a little",
    "G4": "Nausea after tablets, eating fine", "G5": "Health worry mixed with daily chatter",
}

CALM = {"Q_SLURRED": "NO", "Q_AWAKE": "NO", "Q_CHEST": "NO", "Q_ARM_RED": "NO", "Q_TEMP": "98.4", "Q_BANGLES": "NO"}

# answer scripts by story (the same in every language)
ANSWERS = {
    "R1": {"Q_CHEST": "YES"},
    "R2": {"Q_AWAKE": "YES"},
    "R3": {"Q_ARM_RED": "YES"},
    "R4": {"Q_TEMP": "101.5"},
    "R5": {"Q_TEMP": "98.8", "Q_NAUSEA": "YES", "Q_VOMIT": "8"},
    "A1": {"Q_TEMP": "98.6", "Q_BANGLES": "YES", "Q_ARM_SCORE": "4", "Q_ARM_SUDDEN": "NO", "Q_SWOLLEN": "YES",
           "Q_SKIN": "NO"},
    "A2": {"Q_TEMP": "98.6", "Q_HEADACHE": "YES", "Q_HEAD_SCORE": "3", "Q_HEAD_WORSE": "YES"},
    "A3": {"Q_ARM_SCORE": "5", "Q_ARM_SUDDEN": "YES"},
    "A4": {"Q_TIRED_DAYS": "9", "Q_TIRED_WORSE": "NO"},
    "A5": {"Q_TEMP": "98.6", "Q_ARM_SCORE": "6", "Q_ARM_SUDDEN": "NO"},
    "A6": {"Q_TEMP": "98.6"},
    "G1": {"Q_HEADACHE": "YES", "Q_HEAD_SCORE": "2", "Q_HEAD_WORSE": "NO"},
    "G2": {"Q_TIRED_DAYS": "2", "Q_TIRED_WORSE": "NO"},
    "G3": {"Q_ARM_SCORE": "2", "Q_ARM_SUDDEN": "NO"},
    "G4": {"Q_NAUSEA": "YES", "Q_VOMIT": "0", "Q_LIQUIDS": "YES", "Q_SUPP": "NO"},
    "G5": {"Q_HEADACHE": "YES", "Q_HEAD_SCORE": "2", "Q_HEAD_WORSE": "NO", "Q_TIRED_DAYS": "1", "Q_TIRED_WORSE": "NO"},
}

# expected AI tags by story (symptoms, severity word)
TAGS = {
    "R1": (["chest_or_breathing"], "NONE"), "R2": (["sleepiness"], "NONE"),
    "R3": (["arm_swelling_or_redness", "arm_pain"], "NONE"), "R4": (["fever_chills"], "NONE"),
    "R5": (["nausea_vomiting"], "NONE"),
    "A1": (["arm_swelling_or_redness", "arm_pain"], "NONE"), "A2": (["headache"], "NONE"), "A3": (["arm_pain"], "NONE"),
    "A4": (["tiredness"], "NONE"), "A5": (["arm_pain"], "SEVERE"), "A6": (["other_symptom"], "NONE"),
    "G1": (["headache"], "NONE"), "G2": (["tiredness"], "NONE"), "G3": (["arm_pain"], "NONE"),
    "G4": (["nausea_vomiting"], "NONE"), "G5": (["headache", "tiredness"], "NONE"),
}

C = {}


def clip(cid, story, speaker, dialect, caller, text, meaning, **style):
    C[cid] = {"story": story, "speaker": speaker, "dialect": dialect, "caller": caller, "text": text,
              "meaning": meaning, "style": style}


# ---------------------------------------------------------------------------------------------------- Tamil
clip("TA-R01", "R1", "roopa", "Chennai (Madras Bashai)", "patient",
     "இன்னாபா இது… நெஞ்சு நடுவுல பயங்கரமா வலிக்குது, மூச்சு வாங்கவே முடியல… கை காலெல்லாம் வேர்த்து கொட்டுது… "
     "பேஜாரா கீதுபா, சீக்கிரம் ஏதாச்சும் பண்ணுங்க…",
     "What is this… a terrible pain in the middle of my chest, I can't catch my breath… I'm sweating all over… "
     "it's awful, do something quickly…", pace=1.25, noise="street", noise_level=0.4)
clip("TA-R02", "R2", "gokul", "Kongu (Coimbatore)", "son",
     "ஏனுங்க, எங்க அம்மாவுக்கு காலையிலிருந்து நெம்ப தூக்கமாவே இருக்குதுங்க… கண்ணையே தொறக்க முடியலீங்க, "
     "கூப்பிட்டாலும் சரியா பதில் சொல்ல மாட்டேங்குறாங்க… என்ன பண்றதுன்னே தெரியலீங்க.",
     "Sir, my mother has been very sleepy since morning… she can't open her eyes, even when we call her she doesn't "
     "answer properly… we don't know what to do.", pitch=0.9, noise="room", noise_level=0.25)
clip("TA-R03", "R3", "rupali", "Tirunelveli", "patient",
     "ஏலே… எடது கை முழுக்க செவந்து போச்சுலே, தொட்டா நெருப்பா சுடுது… வலியும் தாங்க முடியல… பயமா இருக்குதேன், "
     "என்ன செய்யன்னு தெரியலயே…",
     "Oh… my whole left arm has gone red, it burns like fire to touch… I can't bear the pain… I'm scared, I don't "
     "know what to do…", pace=0.9, pitch=0.93, tremble=True)
clip("TA-R04", "R4", "ritu", "Standard", "patient",
     "ராத்திரியிலிருந்து ஜுரம் அடிக்குது… உடம்பெல்லாம் நடுங்குது, போர்வை போத்தியும் குளிருது… தெர்மாமீட்டர்ல "
     "நூத்தி ஒண்ணுக்கு மேல காட்டுது…",
     "Fever since last night… my whole body is shivering, I feel cold even under a blanket… the thermometer shows "
     "over 101…", pace=0.9, muffled=True)
clip("TA-R05", "R5", "vijay", "Chennai (Madras Bashai)", "son",
     "சார், எங்க அம்மா காலைலேருந்து ஏழெட்டு தடவ வாந்தி எடுத்துட்டாங்க… தண்ணி குடிச்சாலும் உடனே வந்துருது… "
     "ரொம்ப டயர்டா கீறாங்க, இன்னா பண்றதுன்னு சொல்லுங்க சார்.",
     "Sir, my mother has vomited seven or eight times since morning… even water comes right back up… she's very "
     "tired, tell us what to do.", pace=1.2, noise="home", noise_level=0.3)
clip("TA-A01", "A1", "kavitha", "Madurai", "patient",
     "ஆத்தாடி… அ-அய்யோ… ரெண்டு நாளா இந்த எடது கை இம்புட்டு வீங்கிப் போயி கெடக்குய்யா… வளையல்லாம் "
     "இறுகிப் போச்சு, கழட்டவே ஏலலய்யா… ல-லேசா வலிக்குதுய்யா… எனக்கு அம்புட்டு பயமா இருக்கு டாக்டரம்மா, "
     "என்னத்த பண்றதுன்னே தெரியலயே…",
     "Oh mother… oh… for two days this left arm has swollen up this much… the bangles have all gone tight, I just "
     "can't take them off… it hurts a little… I'm so scared, doctor-amma, I don't know what to do…",
     pace=0.85, pitch=0.92, volume=0.9, tremble=True, noise="home", noise_level=0.35)
clip("TA-A02", "A2", "roopa", "Kongu (Coimbatore)", "patient",
     "ஏனுங்க, மூணு நாளா தலைவலி நெம்ப அதிகமாயிருச்சுங்க… போன தடவ சொன்னதைவிட இப்போ கூடுதலா இருக்குதுங்க… "
     "மாத்திரை போட்டும் கேக்கலீங்க.",
     "For three days the headache has got much worse… it's more now than when I told you last time… even the "
     "tablets don't help.", pace=0.95, pitch=0.88)
clip("TA-A03", "A3", "shruti", "Chennai (Madras Bashai)", "patient",
     "நே-நேத்து சாயங்காலம் திடீர்னு எடது கையில சுளீர்னு வலி வந்துச்சுபா… அ-அப்போலேருந்து கையை அசைக்கவே "
     "பயமா கீது…",
     "Yesterday evening a sharp pain suddenly came in my left arm… since then I'm scared to even move it…",
     pace=1.2, tremble=True)
clip("TA-A04", "A4", "kavya", "Tirunelveli", "patient",
     "ஒரு வாரத்துக்கு மேல ஆச்சு, உடம்பு அசதியாவே இருக்குலே… எந்திரிக்கவே முடியல, சோறு கூட சரியா எறங்கல…",
     "It's been more than a week, my body feels exhausted… I can't even get up, even food doesn't go down "
     "properly…", pace=0.85, pitch=0.9, muffled=True)
clip("TA-A05", "A5", "anand", "Standard", "son",
     "அம்மாவுக்கு இடது கையில ரொம்ப கடுமையான வலி, தாங்கவே முடியலன்னு சொல்றாங்க. நான் அவங்க பையன் கார்த்திக் "
     "பேசுறேன்.",
     "Mother says she has very severe pain in her left arm, she can't bear it. This is her son Karthik speaking.",
     noise="street", noise_level=0.45)
clip("TA-A06", "A6", "priya", "Madurai", "patient",
     "முதுகுல எல்லாம் சிவப்பா தடிப்பு தடிப்பா வந்துருச்சுய்யா… அரிப்பு தாங்க முடியல, ராத்திரி தூக்கமே இல்ல…",
     "Red raised patches have come up all over my back… I can't bear the itching, no sleep at night…",
     pace=1.15, noise="home", noise_level=0.3)
clip("TA-G01", "G1", "kavitha", "Standard", "patient",
     "காலையில லேசா தலைவலி இருந்துச்சு, இப்போ கொஞ்சம் பரவாயில்ல. ஒரு தடவை சொல்லிடலாம்னு மெசேஜ் போட்டேன்.",
     "I had a mild headache in the morning, it's a bit better now. I sent a message just to let you know.",
     pitch=0.88)
clip("TA-G02", "G2", "rupali", "Kongu (Coimbatore)", "patient",
     "ரெண்டு நாளா கொஞ்சம் சோர்வா இருக்குதுங்க, வேற ஒண்ணும் இல்லீங்க. சாப்பிட்டு நல்லா ரெஸ்ட் எடுக்கறேனுங்க.",
     "I've been a little tired for two days, nothing else. I'm eating and resting well.",
     muffled=True, noise="room", noise_level=0.3)
clip("TA-G03", "G3", "ritu", "Tirunelveli", "patient",
     "சமையல் பண்ணும்போது கை கொஞ்சம் வலிக்குதுலே, ரொம்ப இல்ல, லேசாத்தான். மத்தபடி நல்லா இருக்கேன்.",
     "My hand hurts a little when I cook, not much, just slightly. Otherwise I'm fine.",
     noise="home", noise_level=0.25)
clip("TA-G04", "G4", "shreya", "Chennai (Madras Bashai)", "patient",
     "மாத்திரை போட்டதும் லேசா குமட்டுது, வாந்தி எல்லாம் இல்லபா. சாப்பாடு நல்லா சாப்பிடறேன்.",
     "I feel slightly queasy after taking the tablets, no vomiting at all. I'm eating well.", pace=1.2)
clip("TA-G05", "G5", "roopa", "Madurai", "patient",
     "நாளைக்கு எங்க பேத்திக்கு காது குத்து விசேஷம்ய்யா, அதுக்கு கிளம்பணும்… நேத்திலேருந்து கொஞ்சம் அசதியா "
     "இருக்கு, லேசா தலையும் வலிக்குது, அம்புட்டுதான்.",
     "Tomorrow is my granddaughter's ear-piercing ceremony, I have to get ready for it… I've been a bit tired since "
     "yesterday, a slight headache too, that's all.", pace=1.05, noise="room", noise_level=0.35)

# ---------------------------------------------------------------------------------------------------- Hindi
clip("HI-R01", "R1", "ritu", "Haryanvi", "patient",
     "राम राम… छाती म्हं घणा दर्द हो रहा सै, साँस कोन्या आ रही… पसीना छूट रहा सै, के करूँ समझ कोन्या आता…",
     "Oh God… there's a lot of pain in my chest, I can't breathe… I'm sweating, I don't understand what to do…",
     pace=1.2, tremble=True)
clip("HI-R02", "R2", "ratan", "Bhojpuri", "son",
     "भइया, हमार माई सबेरे से एकदम बेहोस जइसन सुतल बाड़ी… आँखिए ना खुलत बा, बोलावे पर भी ठीक से जवाब ना देत "
     "बाड़ी।",
     "Brother, my mother has been lying almost unconscious since morning… her eyes won't open, she doesn't answer "
     "properly even when we call her.", pitch=0.9, noise="room", noise_level=0.2)
clip("HI-R03", "R3", "simran", "Mumbaiya", "patient",
     "अरे, बाएँ हाथ पे एकदम लाल लाल हो गया है, छूने पे गरम लगता है… दर्द भी बहुत है, वाट लग गई है अपुन की…",
     "Hey, my left arm has gone completely red, it feels hot to touch… there's a lot of pain too, I'm in a real "
     "mess…", pace=1.25, noise="street", noise_level=0.45)
clip("HI-R04", "R4", "pooja", "Standard", "patient",
     "कल रात से तेज़ बुखार है… पूरा शरीर काँप रहा है, कंबल ओढ़ के भी ठंड लग रही है… थर्मामीटर में एक सौ दो दिखा "
     "रहा है।",
     "High fever since last night… my whole body is shaking, I feel cold even under a blanket… the thermometer "
     "shows 102.", pace=0.85, muffled=True)
clip("HI-R05", "R5", "roopa", "Awadhi", "patient",
     "हमका सुबह से छह-सात बार उल्टी होइ गई है… पानी पियत हैं तौ ऊ भी निकल जात है… बहुत कमजोरी लागत है।",
     "I've vomited six or seven times since morning… when I drink water even that comes back up… I feel very "
     "weak.", pitch=0.9, noise="home", noise_level=0.35)
clip("HI-A01", "A1", "neha", "Bhojpuri", "patient",
     "हाय राम… दू दिन से बायाँ हाथ सूज गइल बा… चूड़ी एतना कस गइल कि निकलते नइखे… त-तनी-तनी दरद भी बा, डर "
     "लागत बा।",
     "Oh God… for two days my left arm has been swollen… the bangles are so tight they won't come off… it hurts a "
     "little too, I'm scared.", pace=0.9, pitch=0.92, tremble=True, noise="home", noise_level=0.3)
clip("HI-A02", "A2", "suhani", "Standard", "patient",
     "तीन दिन से सिर का दर्द बढ़ता जा रहा है… पिछली बार जो बताया था उससे ज़्यादा है… दवाई से भी आराम नहीं।",
     "For three days the headache has been getting worse… it's more than what I told you last time… even the "
     "medicine doesn't help.", pitch=0.88)
clip("HI-A03", "A3", "ishita", "Haryanvi", "patient",
     "क-कल शाम नै अचानक बाएँ हाथ म्हं जोर का दर्द उठ्या… त-तब तै हाथ हिलाते होए डर लागै सै।",
     "Yesterday evening a strong pain suddenly rose in my left arm… since then I'm scared to move my arm.",
     tremble=True)
clip("HI-A04", "A4", "tanya", "Mumbaiya", "patient",
     "एक हफ्ते से ज़्यादा हो गया, एकदम थकान लगती है… उठने का मन नहीं करता, खाना भी बराबर नहीं जाता।",
     "It's been more than a week, I feel completely tired… I don't feel like getting up, and I can't eat "
     "properly either.", pace=0.9, muffled=True)
clip("HI-A05", "A5", "amit", "Standard", "son",
     "नमस्ते, मैं सावित्री जी का बेटा बोल रहा हूँ। माँ के बाएँ हाथ में बहुत ज़्यादा तेज़ दर्द है, बहुत गंभीर लग रहा "
     "है।",
     "Hello, this is Savitri's son. Mother has very sharp pain in her left arm, it looks very serious.",
     noise="street", noise_level=0.45)
clip("HI-A06", "A6", "priya", "Awadhi", "patient",
     "चार दिन से खाँसी आवत है, रात भर खों-खों करत रहित है… बलगम भी निकलत है।",
     "I've had a cough for four days, I keep coughing all night… phlegm is coming up too.",
     pace=1.15, noise="room", noise_level=0.3)
clip("HI-G01", "G1", "rupali", "Bhojpuri", "patient",
     "सबेरे तनी मूड़ी में दरद रहल, अब ठीक बा। बस बतावे खातिर मैसेज कइनी।",
     "I had a slight headache this morning, it's fine now. I sent a message just to let you know.", pitch=0.9)
clip("HI-G02", "G2", "kavya", "Haryanvi", "patient",
     "दो दिन तै थोड़ी थकान सी सै, और कुछ कोन्या। खाणा-पीणा ठीक सै।",
     "I've been a little tired for two days, nothing else. Eating and drinking are fine.",
     muffled=True, noise="room", noise_level=0.3)
clip("HI-G03", "G3", "shreya", "Mumbaiya", "patient",
     "खाना बनाते टाइम हाथ थोड़ा दुखता है, ज़्यादा नहीं, बस हल्का सा। बाकी सब एकदम मस्त है।",
     "My hand hurts a little when I cook, not much, just slightly. Everything else is great.",
     pace=1.1, noise="home", noise_level=0.25)
clip("HI-G04", "G4", "neha", "Standard", "patient",
     "गोली खाने के बाद थोड़ा जी मिचलाता है, उल्टी नहीं हुई। खाना ठीक से खा रही हूँ।",
     "I feel a little queasy after the tablets, I haven't vomited. I'm eating properly.", pace=1.2)
clip("HI-G05", "G5", "pooja", "Awadhi", "patient",
     "परसों पोती का मुंडन है, ओकरे तैयारी में लगी हैं… कल से तनिक थकान है और हल्का सिर दर्द, बस इतना ही।",
     "My granddaughter's head-shaving ceremony is the day after tomorrow, I'm busy getting ready… since yesterday "
     "I'm a bit tired with a slight headache, that's all.", noise="room", noise_level=0.35)

# ---------------------------------------------------------------------------------------------------- Telugu
clip("TE-R01", "R1", "priya", "Telangana", "patient",
     "అయ్యో… చాతీల శానా నొప్పి వస్తాంది, ఊపిరి ఆడతలేదు… చెమటలు పడుతున్నయ్, ఏం జేయాల్నో తెల్వదు…",
     "Oh… a lot of pain in my chest, I can't breathe… I'm sweating, I don't know what to do…",
     pace=1.2, tremble=True)
clip("TE-R02", "R2", "varun", "Coastal Andhra", "son",
     "అండీ, మా అమ్మగారు పొద్దుట్నుంచి బాగా మత్తుగా పడుకునే ఉన్నారు… కళ్ళు తెరవలేకపోతున్నారు, పిలిచినా సరిగ్గా "
     "పలకడం లేదు.",
     "Sir, my mother has been lying very drowsy since morning… she can't open her eyes, she doesn't respond "
     "properly when we call her.", pitch=0.9, noise="room", noise_level=0.2)
clip("TE-R03", "R3", "kavitha", "Rayalaseema", "patient",
     "ఎడమ చెయ్యి మొత్తం ఎర్రగా అయిపోయిందప్పా, ముట్టుకుంటే నిప్పు మాదిరి కాలతాంది… నొప్పి తట్టుకోలేకపోతుండా…",
     "My whole left arm has gone red, it burns like fire when I touch it… I can't bear the pain…",
     pitch=0.93, tremble=True)
clip("TE-R04", "R4", "ritu", "Standard", "patient",
     "నిన్న రాత్రి నుంచి జ్వరం బాగా ఉంది… ఒళ్ళంతా వణుకుతోంది, దుప్పటి కప్పుకున్నా చలిగా ఉంది… థర్మామీటర్‌లో "
     "నూట ఒకటిన్నర చూపిస్తోంది.",
     "High fever since last night… my whole body is shivering, I feel cold even under the blanket… the thermometer "
     "shows 101.5.", pace=0.85, muffled=True)
clip("TE-R05", "R5", "mohit", "Hyderabadi", "son",
     "సార్, మా అమ్మకి పొద్దున్నుంచి ఏడెనిమిది సార్లు వాంతులైనయ్… నీళ్ళు తాగినా వెంటనే బయటికొచ్చేస్తున్నయ్… "
     "బహుత్ కమ్జోర్ అయిపోయింది.",
     "Sir, my mother has vomited seven or eight times since morning… even water comes straight back out… she has "
     "become very weak.", pace=1.2, noise="street", noise_level=0.4)
clip("TE-A01", "A1", "suhani", "Coastal Andhra", "patient",
     "అయ్యో బాబోయ్… రెండ్రోజుల్నించి ఎడమ చెయ్యి వాచిపోయిందండీ… గాజులు బిగుసుకుపోయి తీయలేకపోతున్నానండీ… "
     "కొ-కొంచెం నొప్పిగా కూడా ఉంది, భయంగా ఉందండీ.",
     "Oh my… for two days my left arm has been swollen… the bangles are so tight I can't take them off… it hurts a "
     "little too, I'm scared.", pace=0.9, pitch=0.92, tremble=True, noise="home", noise_level=0.3)
clip("TE-A02", "A2", "roopa", "Telangana", "patient",
     "మూడు దినాల సంది తలనొప్పి ఎక్కువైతాంది… మొన్న జెప్పినదానికన్నా జెర ఎక్కువే ఉంది… గోలీ ఏసుకున్నా "
     "తగ్గుతలేదు.",
     "For three days the headache has been getting worse… it's a bit more than what I told you the other day… "
     "even the tablet doesn't help.", pitch=0.88)
clip("TE-A03", "A3", "shruti", "Rayalaseema", "patient",
     "ని-నిన్న సాయంత్రం ఉన్నట్టుండి ఎడమ చేతిలో గట్టిగా నొప్పి వచ్చిందప్పా… అప్పటి నుంచి చెయ్యి కదిలించాలంటే "
     "భయమైతాంది.",
     "Yesterday evening a strong pain suddenly came in my left arm… since then I'm scared to move the arm.",
     pace=1.15, tremble=True)
clip("TE-A04", "A4", "kavya", "Coastal Andhra", "patient",
     "వారం దాటిపోయిందండీ, ఒళ్ళంతా నీరసంగానే ఉంది… లేవాలన్నా ఓపిక లేదు, అన్నం కూడా సరిగ్గా సయించడం లేదండీ.",
     "It's been more than a week, my whole body feels weak… I don't have the strength to get up, and I can't eat "
     "properly either.", pace=0.85, muffled=True)
clip("TE-A05", "A5", "aditya", "Hyderabadi", "son",
     "నమస్తే, నేను నిర్మల గారి అబ్బాయిని. అమ్మ ఎడమ చేతిలో చాలా తీవ్రమైన నొప్పి, భరించలేకపోతోంది.",
     "Hello, I'm Nirmala's son. Mother has very severe pain in her left arm, she can't bear it.",
     noise="street", noise_level=0.45)
clip("TE-A06", "A6", "neha", "Telangana", "patient",
     "నాలుగు దినాల సంది కాళ్ళు వాపులు వచ్చినయ్, చీలమండల దగ్గర బాగా ఉబ్బినయ్…",
     "For four days my legs have been swelling, they're very puffy around the ankles…",
     pace=1.15, noise="home", noise_level=0.3)
clip("TE-G01", "G1", "rupali", "Standard", "patient",
     "పొద్దున కొంచెం తలనొప్పిగా ఉండేది, ఇప్పుడు పర్వాలేదు. ఊరికే చెప్దామని మెసేజ్ పెట్టాను.",
     "I had a slight headache in the morning, it's fine now. I sent a message just to let you know.", pitch=0.88)
clip("TE-G02", "G2", "ishita", "Rayalaseema", "patient",
     "రెండు దినాలుగా కొంచెం నీరసంగా ఉండాది, ఇంకేమీ లేదు. బాగానే తింటాండా.",
     "I've been a little tired for two days, nothing else. I'm eating well.",
     muffled=True, noise="room", noise_level=0.3)
clip("TE-G03", "G3", "tanya", "Telangana", "patient",
     "వంట చేసేటప్పుడు చెయ్యి జెర నొస్తాంది, ఎక్కువేం లేదు, కొంచెమే. మిగతా అంతా మంచిగనే ఉన్న.",
     "My hand hurts a bit when I cook, not much, just slightly. Everything else is fine.",
     noise="home", noise_level=0.25)
clip("TE-G04", "G4", "pooja", "Coastal Andhra", "patient",
     "మాత్ర వేసుకున్నాక కొంచెం వికారంగా ఉంటుందండీ, వాంతులు ఏమీ లేవు. అన్నం బాగానే తింటున్నాను.",
     "I feel a little queasy after taking the tablet, no vomiting at all. I'm eating well.", pace=1.2)
clip("TE-G05", "G5", "simran", "Hyderabadi", "patient",
     "ఎల్లుండి మా మనవరాలి పుట్టినరోజు ఫంక్షన్ ఉంది, దానికి రెడీ అవ్వాలి… నిన్నటి నుంచి కొంచెం అలసటగా ఉంది, "
     "కొంచెం తలనొప్పి కూడా, అంతే.",
     "My granddaughter's birthday function is the day after tomorrow, I have to get ready… I've been a bit tired "
     "since yesterday, with a slight headache too, that's all.", noise="room", noise_level=0.35)

LANGUAGE = {"TA": "Tamil", "HI": "Hindi", "TE": "Telugu"}
PATIENT = {"Tamil": "SYN-0142", "Hindi": "SYN-0011", "Telugu": "SYN-0013"}
TIER = {"R": "Red", "A": "Amber", "G": "Green"}


def clips() -> dict:
    out = {}
    for cid, c in C.items():
        lang = LANGUAGE[cid[:2]]
        symptoms, severity = TAGS[c["story"]]
        out[cid] = {**c, "language": lang, "mrn": PATIENT[lang], "tier": TIER[c["story"][0]],
                    "story_text": STORIES[c["story"]], "tags": {"symptoms": symptoms, "severity_word": severity},
                    "answers": {**CALM, **ANSWERS[c["story"]]}}
    return out
