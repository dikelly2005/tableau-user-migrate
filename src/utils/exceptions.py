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


class PermissionError(TableauMigrateError):
    pass


class ContentNotFoundError(TableauMigrateError):
    pass


class ValidationError(TableauMigrateError):
    pass


class ConfigurationError(TableauMigrateError):
    pass
