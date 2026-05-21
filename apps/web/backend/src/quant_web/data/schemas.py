"""Request DTOs for the data module (doc §5 / §6). Responses are shaped dicts."""
from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field


# --- batches ---------------------------------------------------------------
class InvalidateRequest(BaseModel):
    reason: str = Field(min_length=10, max_length=500)


class SupersedeRequest(BaseModel):
    old_batch_id: str
    new_batch_id: str


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


class DiffRequest(BaseModel):
    table: str
    source_a: str
    source_b: str
    start: dt.date | None = None
    end: dt.date | None = None
    columns: list[str] | None = None
    sample: int = Field(default=20, ge=1, le=1000)


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
class VacuumPreviewRequest(BaseModel):
    tables: list[str] | None = None
    older_than_days: int = Field(default=90, ge=0)


class VacuumExecuteRequest(BaseModel):
    tables: list[str] | None = None
    older_than_days: int = Field(default=90, ge=0)
    confirm: str


class CompactPreviewRequest(BaseModel):
    tables: list[str] | None = None


class CompactExecuteRequest(BaseModel):
    tables: list[str] | None = None
    confirm: str = "COMPACT"


# --- charts ----------------------------------------------------------------
class KlineMultiRequest(BaseModel):
    symbols: list[str] = Field(max_length=5)
    frequency: str = "1d"
    adjust: Literal["none", "pre", "post"] = "pre"
    start: dt.date | None = None
    end: dt.date | None = None
    mode: Literal["grid", "overlay"] = "overlay"


class ExplorerRequest(BaseModel):
    table: str
    x_axis: str
    y_axes: list[str]
    group_by: str | None = None
    aggregation: Literal[
        "none", "daily_avg", "weekly_avg", "monthly_avg", "sum", "count", "min", "max"
    ] = "none"
    chart_type: Literal[
        "line", "bar", "scatter", "area", "stacked_bar", "heatmap", "pie"
    ] = "line"
    filters: dict | None = None
    limit: int = Field(default=10000, ge=1, le=200000)


class ChartTemplateCreate(BaseModel):
    name: str
    chart_type: str | None = None
    config: dict
