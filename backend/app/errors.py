"""Domain errors. Raised inside a request's transaction, so the transaction
rolls back and nothing (data or audit row) is kept; main.py maps them to HTTP."""


class DomainError(Exception):
    status = 400

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class NotFound(DomainError):
    status = 404


class Conflict(DomainError):
    status = 409


class Invalid(DomainError):
    status = 422


class Forbidden(DomainError):
    status = 403
