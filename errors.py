class ApiError(Exception):
    """Error that is rendered as a JSON response by the app error handler."""

    def __init__(self, status, code, message, details=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or []


def validation_error(message, details=None):
    return ApiError(422, "validation_error", message, details)
