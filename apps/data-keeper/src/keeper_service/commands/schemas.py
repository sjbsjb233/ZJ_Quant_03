"""Request DTOs for the keeper write-command API (subset of quant_web.data.schemas
— only the *write* shapes; reads stay in web). Kept byte-compatible so a request
the web proxy forwards validates identically here."""
from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field


# --- sources ---------------------------------------------------------------
class SourceCreate(BaseModel):
    source_id: str = Field(pattern=r"^[a-z0-9_]+$")
    display_name: str
    category: Literal["external_api", "manual", "internal"] = "external_api"
    priority: int = 100
    is_active: bool = True
    field_mapping: dict | None = None
    rate_limit_per_min: int | None = None
    auth_method: str | None = None
    endpoint: str | None = None
    description: str | None = None


class SourceUpdate(BaseModel):
    display_name: str | None = None
    category: Literal["external_api", "manual", "internal"] | None = None
    priority: int | None = None
    is_active: bool | None = None
    field_mapping: dict | None = None
    rate_limit_per_min: int | None = None
    auth_method: str | None = None
    endpoint: str | None = None
    description: str | None = None


# --- fields ----------------------------------------------------------------
class FieldUpdate(BaseModel):
    description: str | None = None
    unit: str | None = None
    authoritative_source: str | None = None
    consumers: list[str] | None = None


class PromoteRequest(BaseModel):
    status: Literal["promoted", "core", "extras", "deprecated"] = "promoted"
    consumers: list[str] | None = None
    authoritative_source: str | None = None


class MigrateRequest(BaseModel):
    dry_run: bool = False
    dtype: str | None = None


class AddConsumerRequest(BaseModel):
    consumer: str


# --- quality ---------------------------------------------------------------
class AckRequest(BaseModel):
    acknowledged: bool = True
    ack_reason: str | None = None
    ack_expires_at: dt.datetime | None = None


# --- maintenance -----------------------------------------------------------
class VacuumExecuteRequest(BaseModel):
    tables: list[str] | None = None
    older_than_days: int = Field(default=90, ge=0)
    confirm: str


class CompactExecuteRequest(BaseModel):
    tables: list[str] | None = None
    confirm: str = "COMPACT"
