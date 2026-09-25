from pydantic import BaseModel


class StatusCheck(BaseModel):
    status: bool
    detail: str
