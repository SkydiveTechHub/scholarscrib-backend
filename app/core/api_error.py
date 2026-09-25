from enum import Enum

from pydantic import BaseModel
from starlette.responses import JSONResponse, Response


class CustomApiError(Exception):
    def get_response(self) -> Response:
        raise NotImplementedError()


class ApiError(CustomApiError):
    # Subclass this to add an implementation and pass API_SERVICE_ERRORS.
    def __init__(self, error_enum: Enum, extra_detail: str = "") -> None:
        # use this format to setup errors for your service
        # class ErrorEnum(Enum):
        # MP_000=("Unknown Api Error", status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.error_code = error_enum.name
        self.message = error_enum.value[0]
        self.status_code = error_enum.value[1]
        self.extra_detail = extra_detail or error_enum.value[2]

    def get_response(self) -> Response:
        error = {
            "code": self.error_code,
            "message": self.message,
            "extra_detail": self.extra_detail,
        }

        return JSONResponse(error, status_code=self.status_code)


class ErrorResponse(BaseModel):
    code: str
    message: str
    extra_detail: str

    class Config:
        populate_by_name = True


class ApiErrorResponse(BaseModel):
    errors: list[ErrorResponse]

    class Config:
        populate_by_name = True
