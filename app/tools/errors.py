class ToolError(Exception):
    code = "TOOL_ERROR"
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.retryable = retryable
    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "retryable": self.retryable}

class NotFoundError(ToolError):
    code = "NOT_FOUND"

class InvalidInputError(ToolError):
    code = "INVALID_INPUT"

class DataSourceError(ToolError):
    code = "DATA_SOURCE"