from sqlalchemy import String, JSON, Double, Integer, Boolean, ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class BikeRequest(Base):
    """求车接报：一次扫码一行；同一求车编号可有多行。"""
    __tablename__ = 'bike_requests'
    __table_args__ = (
        UniqueConstraint('request_id', 'scan_time', 'seq', name='uq_bike_request_scan'),
        Index('ix_bike_request_cutoff', 'scan_time'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(String(80))
    scan_time: Mapped[float] = mapped_column(Double)
    station: Mapped[str] = mapped_column(String(120))
    result: Mapped[str] = mapped_column(String(20))          # borrowed / shortage
    seq: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(200), default='manual.json')
    received_at: Mapped[float] = mapped_column(Double)
    # 结算归属：null 表示尚未进入任何结算批次
    settlement_id: Mapped[int | None] = mapped_column(ForeignKey('settlement_runs.id'), nullable=True)


class SettlementRun(Base):
    """结算批次：一次截止点取数生成一版，幂等不重复。"""
    __tablename__ = 'settlement_runs'
    __table_args__ = (
        UniqueConstraint('cutoff', name='uq_settlement_cutoff'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    cutoff: Mapped[float] = mapped_column(Double)
    window_start: Mapped[float] = mapped_column(Double, default=0.0)
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey('datasets.id'), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default='running')   # running / done
    # 该截止点下进入视野的全部求车单号（含晚到之外的全部已接报分组）
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(150), default='system')
    created_at: Mapped[float] = mapped_column(Double)
    finished_at: Mapped[float | None] = mapped_column(Double, nullable=True)


class SettlementItem(Base):
    """结算明细：每个求车编号在某截止点下的一条归属结果。"""
    __tablename__ = 'settlement_items'
    __table_args__ = (
        UniqueConstraint('settlement_id', 'request_id', name='uq_settlement_item'),
        Index('ix_settlement_group', 'station', 'period'),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    settlement_id: Mapped[int] = mapped_column(ForeignKey('settlement_runs.id'))
    request_id: Mapped[str] = mapped_column(String(80))
    station: Mapped[str] = mapped_column(String(120))   # 首次扫码站点
    period: Mapped[int] = mapped_column(Integer)        # 首次扫码时段
    borrowed: Mapped[bool] = mapped_column(Boolean)
    borrow_station: Mapped[str | None] = mapped_column(String(120), nullable=True)
    borrow_period: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20))     # settled / held
    matched: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str] = mapped_column(String(300), default='')
    scan_count: Mapped[int] = mapped_column(Integer, default=1)
