"""Read models and actions behind the prototype v7.2 screens: worklist, timeline, calls, pause,
send reminder, department lists, import preview, Care Plan, indicators, audit search."""
from conftest import ANAND, PRIYA, SURESH, advance, confirm_plan, morning, outbox, plan_body, scalar


def worklist(client):
    r = client.get("/worklist", headers=PRIYA)
    assert r.status_code == 200, r.text
    return r.json()


def row(data, mrn):
    return next(p for p in data["patients"] if p["mrn"] == mrn)


def test_worklist_after_reset(seeded, client):
    data = worklist(client)
    assert len(data["patients"]) == 25
    assert data["coordinators"] == ["Meera Joseph", "Priya Nair"]
    assert row(data, "SYN-0007")["coordinator"] == "Priya Nair"
    assert row(data, "SYN-0008")["coordinator"] == "Meera Joseph"
    assert row(data, "SYN-0003")["main"] == "action"          # consent without automated messaging
    assert row(data, "SYN-0013")["main"] == "action"          # no consent on record
    assert row(data, "SYN-0007")["main"] == "verify"          # plan not confirmed yet
    assert data["glance"] == {"agent_actions": 0, "agent_patients": 0, "handled": 0, "approvals": 0, "need_you": 2}
    assert client.get("/worklist", headers=ANAND).status_code == 403


def test_worklist_follows_the_reminder(seeded, client):
    confirm_plan(client, "SYN-0007")
    morning(client)
    r = row(worklist(client), "SYN-0007")
    assert r["main"] == "agent"
    assert "agent sent a booking reminder" in r["reasons"][0]["txt"]
    data = worklist(client)
    assert data["glance"]["handled"] == 1 and data["glance"]["agent_actions"] == 2
    assert {f["mrn"] for f in data["feed"]} == {"SYN-0007"}
    advance(client, 4)           # Fri 6 Nov = T-3 for T1, still not booked
    morning(client)
    r = row(worklist(client), "SYN-0007")
    assert r["main"] == "action" and any("T-3" in x["txt"] for x in r["reasons"])


def test_worklist_handover_and_no_response(seeded, client):
    confirm_plan(client, "SYN-0007")
    morning(client)
    advance(client, 7)
    morning(client)
    r = row(worklist(client), "SYN-0007")
    assert any(x["tag"] == "noresp" and x["txt"] == "2 messages in a row unanswered" for x in r["reasons"])
    client.post("/simulator/SYN-0007/reply", headers=PRIYA, json={"text": "pain"})
    r = row(worklist(client), "SYN-0007")
    assert r["main"] == "action" and r["handover_id"]
    assert r["reasons"][0]["txt"].startswith("Agent stopped")
    assert not any(x["tag"] == "noresp" for x in r["reasons"])  # the reply ends the unanswered run


def test_pause_until_and_automatic_resume(seeded, client):
    assert client.post("/patients/SYN-0007/pause", headers=PRIYA, json={"until": "2026-11-01", "reason": "x"}).status_code == 422
    assert client.post("/patients/SYN-0007/pause", headers=PRIYA, json={"until": "2026-11-05"}).status_code == 422
    r = client.post("/patients/SYN-0007/pause", headers=PRIYA, json={"until": "2026-11-05", "reason": "Travelling"})
    assert r.status_code == 200
    w = row(worklist(client), "SYN-0007")
    assert w["main"] == "paused" and "until 05 Nov 2026 — Travelling" in w["reasons"][0]["txt"]
    advance(client, 3)
    assert morning(client)["pauses_ended"] == ["SYN-0007"]
    assert client.get("/patients/SYN-0007", headers=PRIYA).json()["status"] == "Active"


def test_calls(seeded, client):
    assert client.post("/patients/SYN-0007/calls", headers=PRIYA, json={"outcome": "concern"}).status_code == 422
    r = client.post("/patients/SYN-0007/calls", headers=PRIYA,
                    json={"outcome": "concern", "note": "Swelling in the arm for two weeks"})
    assert r.status_code == 201 and r.json()["handover_id"]
    hid = r.json()["handover_id"]
    assert client.get(f"/handovers/{hid}", headers=PRIYA).json()["source"] == "Phone call by Priya Nair"
    client.post(f"/handovers/{hid}/gate1", headers=PRIYA, json={"choice": "send_to_doctor"})
    client.post(f"/handovers/{hid}/gate2", headers=ANAND, json={"decision": "urgent"})
    client.post("/patients/SYN-0007/calls", headers=PRIYA, json={"outcome": "book", "note": "Coming Saturday"})
    h = client.get(f"/handovers/{hid}", headers=PRIYA).json()
    assert h["called_on"] == "2026-11-02"
    assert not any(t["task_type"] == "urgent_call" and t["status"] == "open" for t in h["tasks"])


def test_send_reminder_by_a_person(seeded, client):
    assert client.post("/patients/SYN-0007/remind", headers=PRIYA).status_code == 409  # no cards yet
    confirm_plan(client, "SYN-0007")
    r = client.post("/patients/SYN-0007/remind", headers=PRIYA)
    assert r.status_code == 201 and (r.json()["actor"], r.json()["status"]) == ("u_priya", "sent")


def test_department_lists(seeded, client):
    confirm_plan(client, "SYN-0007")
    lists = client.get("/department-lists", headers=PRIYA).json()
    assert [(d["department"], [i["item_code"] for i in d["items"]]) for d in lists] == [("Laboratory", ["T1"])]


def test_timeline_traces_origins(seeded, client):
    t = client.get("/patients/SYN-0006/timeline", headers=ANAND).json()
    appts = [e for e in t["events"] if e["label"].startswith("Appointment")]
    assert len(appts) == 2 and all(e["origin"] is None for e in appts)  # nothing on file explains them yet
    confirm_plan(client, "SYN-0006")
    t = client.get("/patients/SYN-0006/timeline", headers=ANAND).json()
    appts = [e for e in t["events"] if e["label"].startswith("Appointment")]
    assert {e["origin"] for e in appts} == {"Order card V1", "Order card T1"}
    assert t["milestones"] >= 3 and t["traced"] == t["need_origin"]
    assert client.get("/patients/SYN-0006/timeline", headers=SURESH).status_code == 403


def test_import_preview_keeps_nothing(seeded, client):
    batches, audits = scalar("SELECT count(*) FROM import_batch"), scalar("SELECT count(*) FROM audit_ledger")
    r = client.post("/imports?preview=true", headers=PRIYA, data={"source_type": "register"},
                    files={"file": ("x.csv", "mrn,full_name,phone,caregiver_name,caregiver_phone,preferred_language\n"
                                             "SYN-0050,New Person,+919000000050,,,Hindi\n")})
    assert r.status_code == 201 and r.json()["preview"] is True and r.json()["rows_created"] == 1
    assert scalar("SELECT count(*) FROM import_batch") == batches
    assert scalar("SELECT count(*) FROM audit_ledger") == audits
    assert scalar("SELECT count(*) FROM patient WHERE mrn = 'SYN-0050'") == 0


def test_care_plan_and_indicators(seeded, client):
    plan = client.get("/care-plan", headers=ANAND).json()
    assert [t["name"] for t in plan["message_templates"]] and plan["autonomy_policy"][0]["body"]["tasks"]
    assert client.get("/care-plan", headers=SURESH).status_code == 403
    confirm_plan(client, "SYN-0007")
    morning(client)
    k = client.get("/kpis", headers=SURESH)
    body = k.json()
    assert body["recall_completion"] is None and body["baseline"] is None  # never invented
    assert body["agent_actions"] > 0 and body["g0_gaps"] == 1 and body["messages_sent"] == 2
    assert "SYN-" not in k.text and "Sunita" not in k.text


def test_audit_search_and_recent_decisions(seeded, client):
    confirm_plan(client, "SYN-0007")
    morning(client)
    hid = client.post("/simulator/SYN-0007/reply", headers=PRIYA, json={"text": "pain"}).json()["handover_id"]
    client.post(f"/handovers/{hid}/gate1", headers=PRIYA, json={"choice": "send_to_doctor"})
    client.post(f"/handovers/{hid}/gate2", headers=ANAND, json={"decision": "routine"})
    only = client.get("/audit?mrn=SYN-0007&limit=400").json()
    assert only["entries"] and all(e["mrn"] == "SYN-0007" for e in only["entries"])
    agent = client.get("/audit?mrn=SYN-0007&kind=agent").json()
    assert agent["entries"] and {e["actor_type"] for e in agent["entries"]} == {"agent"}
    assert agent["counts"]["agent"] == len(agent["entries"])
    found = client.get("/audit?q=gate2").json()["entries"]
    assert [e["action"] for e in found] == ["gate2_decided"]
    recent = client.get("/decisions/recent", headers=ANAND).json()
    assert [(d["mrn"], d["gate2_decision"], d["gate2_on"]) for d in recent] == [("SYN-0007", "routine", "2026-11-02")]
    assert outbox("SYN-0007")[0]["template_id"] == "test_booking_reminder"  # T1 is a test (Use Case 2T v1.4)
    assert plan_body("SYN-0007")["template_id"] == "BC-SURV-1"


def test_switching_a_task_to_shadow_is_a_new_policy_version(seeded, client):
    assert client.post("/care-plan/autonomy/log_button_reply", headers=PRIYA, json={"mode": "shadow"}).status_code == 404
    assert client.post("/care-plan/autonomy/booking_reminder", headers=ANAND, json={"mode": "shadow"}).status_code == 403
    r = client.post("/care-plan/autonomy/booking_reminder", headers=PRIYA, json={"mode": "shadow"})
    assert r.json() == {"task": "booking_reminder", "mode": "shadow", "version": 2}
    assert scalar("SELECT count(*) FROM config_versions WHERE kind = 'autonomy_policy' AND status = 'active'") == 1
    confirm_plan(client, "SYN-0007")
    actions = morning(client)["actions"]
    assert {a["outcome"] for a in actions} == {"pending_approval"}
    assert scalar("SELECT policy_version FROM audit_ledger WHERE action = 'agent_decision' ORDER BY id DESC LIMIT 1") == "2.0"


def test_audit_date_range(seeded, client):
    """From/To filter on the demo date shown on each entry, both ends included; From after To is refused."""
    confirm_plan(client, "SYN-0007")
    advance(client, 7)
    morning(client)
    day = client.get("/audit?from=2026-11-09&to=2026-11-09&limit=400").json()
    assert day["entries"] and {e["demo_date"] for e in day["entries"]} == {"2026-11-09"}
    assert "gap_detected" in {e["action"] for e in day["entries"]}
    before = client.get("/audit?to=2026-11-08&limit=400").json()["entries"]
    assert before and all(e["demo_date"] <= "2026-11-08" for e in before)
    assert "plan_confirmed" in {e["action"] for e in before}
    combined = client.get("/audit?from=2026-11-09&to=2026-11-09&q=gap_detected&mrn=SYN-0007").json()
    assert combined["entries"] and {e["action"] for e in combined["entries"]} == {"gap_detected"}
    assert client.get("/audit?from=2026-11-10&to=2026-11-09").status_code == 422
    assert client.get("/audit?from=09-11-2026").status_code == 422
