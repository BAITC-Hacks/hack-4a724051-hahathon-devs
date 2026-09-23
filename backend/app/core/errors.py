class AppError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retryable = retryable


def not_found() -> AppError:
    return AppError("not_found", "Ресурс не найден.", 404)


def unavailable() -> AppError:
    return AppError(
        "requires_integration", "Эта возможность пока не подключена.", 503, True
    )
