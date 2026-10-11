"""Demo personas for the "Viewing as" role switcher (prototype v7.2).
Demo profile: no login; the role only shapes the UI and the audit actor."""

ROLES = [
    {"id": "coordinator", "label": "Care coordinator", "person": "Priya Nair"},
    {"id": "oncologist", "label": "Oncologist", "person": "Dr. Ananya Raghunathan"},
    {"id": "chief", "label": "Chief of hospital", "person": "Dr. Mohan Rao"},
    {"id": "reviewer", "label": "External reviewer", "person": "Demo reviewer"},
]

ROLES_BY_ID = {role["id"]: role for role in ROLES}
