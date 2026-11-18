from typing import Literal

from pydantic import BaseModel, Field


class RequestEventInput(BaseModel):
    request_id: str = Field(min_length=1, max_length=80)
    scan_time: float = Field(ge=0)
    station: str = Field(min_length=1, max_length=120)
    result: Literal['borrowed', 'shortage']
    seq: int = Field(default=0, ge=0)


class RequestIntakeInput(BaseModel):
    source: str = Field(default='manual.json', max_length=200)
    records: list[RequestEventInput] = Field(min_length=1, max_length=20000)


class SettlementInput(BaseModel):
    # 截止点（秒级时间戳）；不传则用服务端当前时刻
    cutoff: float | None = Field(default=None, ge=0)
    # 观测窗口起点（秒），扫码时刻折算相对时段的锚点；默认 0
    window_start: float = Field(default=0.0, ge=0)
    dataset_id: int | None = Field(default=None)
