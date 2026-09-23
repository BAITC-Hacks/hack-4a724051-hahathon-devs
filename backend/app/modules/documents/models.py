from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.contracts import DTO


class AssetView(DTO):
    id: UUID
    name: str
    content_type: str
    size_bytes: int
    status: Literal["ready", "partial", "quarantined"]
    warnings: list[str]
    created_at: float


@dataclass(frozen=True)
class OwnedImage:
    asset_id: UUID
    mime_type: str
    content: bytes
