from clawos_cli.domain.error_codes import ErrorCode


class ClawOSError(RuntimeError):
    def __init__(self, code: ErrorCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)

    def to_user_message(self) -> str:
        return f"clawos error: [{self.code}] {self.message}"

