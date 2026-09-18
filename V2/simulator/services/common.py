import hashlib
import hmac
import json
from datetime import datetime
from typing import Literal

from fastapi import Header, HTTPException
from pydantic import BaseModel, Field

from backend.app.config import PROJECT_ROOT
from backend.app.database import get_connection

SERVICE_TOKEN_FILE = PROJECT_ROOT / ".local" / "service_tokens.json"


class OrderCreate(BaseModel):
    shop_id: str
    external_order_id: str
    sku: str
    quantity: int = Field(gt=0)
    amount_minor: int = Field(ge=0)
    payment_status: str = "paid"


class OrderEvent(OrderCreate):
    event_id: str
    company_id: str


class ShopSyncUpdate(BaseModel):
    enabled: bool


class ConnectionUpdate(BaseModel):
    status: Literal["authorized", "auth_expired", "forbidden", "rate_limited", "internal_error", "unavailable", "timeout"]


class WarehouseStockUpdate(BaseModel):
    physical_quantity: int = Field(ge=0)
    reserved_quantity: int = Field(ge=0)


class StockPublishRequest(BaseModel):
    shop_id: str
    platform_sku: str


class PlatformStockUpdate(BaseModel):
    request_id: str
    company_id: str
    shop_id: str
    platform_sku: str
    quantity: int = Field(ge=0)
    source_version: int = Field(gt=0)


class OrderRepairRequest(BaseModel):
    action_id: str
    request_id: str
    company_id: str
    shop_id: str
    external_order_id: str
    source_event_id: str
    source_version: int
    shop_version: int
    source_snapshot: dict[str, object]
    enable_order_sync: bool = False
    approval_id: str
    approved_by: str
    approval_expires_at: datetime


class WarehouseOrderRequest(BaseModel):
    merchant_order_id: str
    company_id: str
    shop_id: str
    external_order_id: str
    merchant_sku: str
    quantity: int = Field(gt=0)


class ShipmentCreate(BaseModel):
    shop_id: str
    external_order_id: str
    carrier: str
    tracking_number: str


class ShipmentEvent(BaseModel):
    shipment_id: str
    company_id: str
    shop_id: str
    external_order_id: str
    carrier: str
    tracking_number: str
    version: int
    shipped_at: datetime


class PlatformShipmentUpdate(ShipmentEvent):
    request_id: str


class ShipmentRepairRequest(BaseModel):
    action_id: str
    request_id: str
    company_id: str
    shop_id: str
    external_order_id: str
    shipment_id: str
    shipment_version: int
    source_snapshot: dict[str, object]
    enable_shipment_sync: bool = False
    approval_id: str
    approved_by: str
    approval_expires_at: datetime


class BusinessAuth(BaseModel):
    company_id: str
    user_id: str
    role: str


def payload_hash(event: OrderEvent) -> str:
    payload = event.model_dump(exclude={"event_id"})
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def read_service_token(name: str) -> str:
    try:
        values = json.loads(SERVICE_TOKEN_FILE.read_text(encoding="utf-8"))
        return str(values[name])
    except (FileNotFoundError, KeyError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Missing local service token: {name}") from error


def require_token(provided: str | None, name: str) -> None:
    expected = read_service_token(name)
    if provided is None or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid service credential")


def current_user(authorization: str | None = Header(default=None)) -> BusinessAuth:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Bearer token is required")
    token = authorization.removeprefix("Bearer ").strip()
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with get_connection() as connection:
        row = connection.execute("SELECT u.company_id, u.user_id, u.role FROM support.access_tokens t JOIN support.users u ON u.user_id = t.user_id WHERE t.token_hash = %s AND t.revoked_at IS NULL", (token_hash,)).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="Invalid bearer token")
    return BusinessAuth(**row)
