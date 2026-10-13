"""Who is calling. Demo profile: no login; the X-User header (set by the
"Viewing as" switcher) picks one of the synthetic users. Checked without the
database, so 401/403 answer even while it is off."""
from fastapi import Header, HTTPException

from .reference import USERS_BY_ID


def current_user(x_user: str | None = Header(default=None)) -> dict:
    user = USERS_BY_ID.get(x_user or "")
    if user is None:
        raise HTTPException(status_code=401, detail="Unknown or missing user (X-User header)")
    return user


def require_role(*roles: str):
    def dependency(x_user: str | None = Header(default=None)) -> dict:
        user = current_user(x_user)
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail=f"Not allowed for role {user['role']}")
        return user

    return dependency


COORDINATOR = ("coordinator",)
CLINICAL = ("coordinator", "oncologist")
ANY_ROLE = ("coordinator", "oncologist", "chief")
