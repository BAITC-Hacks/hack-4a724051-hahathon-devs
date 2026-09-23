from fastapi import Request

from app.contracts import Envelope, Meta


def success(request: Request, data):
    return Envelope(data=data, meta=Meta(request_id=request.state.request_id))
