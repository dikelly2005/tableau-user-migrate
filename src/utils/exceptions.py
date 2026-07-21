class TableauMigrateError(Exception):
    pass


class AuthenticationError(TableauMigrateError):
    pass


class APIError(TableauMigrateError):
    def __init__(self, message: str, status_code: int = None, response_body: str = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class RateLimitError(APIError):
    pass


class UserNotFoundError(TableauMigrateError):
    pass


class UserAlreadyExistsError(TableauMigrateError):
    pass


class TableauPermissionError(TableauMigrateError):
    pass


class ContentNotFoundError(TableauMigrateError):
    pass


class ValidationError(TableauMigrateError):
    pass


class ConfigurationError(TableauMigrateError):
    pass


def is_conflict_error(exc: Exception) -> bool:
    if isinstance(exc, APIError) and exc.status_code == 409:
        return True
    exc_str = str(exc).lower()
    return "409" in exc_str or "already exists" in exc_str
