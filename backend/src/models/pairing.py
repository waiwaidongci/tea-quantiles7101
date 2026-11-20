"""两册杯签遗失的配对研究：草稿读数、冻结运行与复核。

并发语义
--------
``PairingStudy.revision`` 在每次“更正”事务中自增，且一次更正对两册的改动
在同一事务内提交。发起研究时先读 revision、读两册、再读 revision；两次
revision 不同就重试，因此冻结的初测与复测必然同属某次更正之前或之后，
不会出现一册已更正、另一册尚未更正的撕裂快照。运行一旦冻结，worker 只读
快照，后续更正不会改写历史结果；同一快照重跑得到逐字节一致的结果。
"""
from sqlalchemy import Column, Integer, String, JSON, Text, ForeignKey, UniqueConstraint
from src.config.database import Base


class PairingStudy(Base):
    __tablename__ = 'pairing_studies'
    id = Column(Integer, primary_key=True)
    name = Column(String(120), unique=True, nullable=False)
    description = Column(Text, default='')
    revision = Column(Integer, nullable=False, default=0)
    created_at = Column(String(40), nullable=False)


class PairingReading(Base):
    __tablename__ = 'pairing_readings'
    __table_args__ = (UniqueConstraint('pairing_study_id', 'booklet', 'source_key'),)
    id = Column(Integer, primary_key=True)
    pairing_study_id = Column(Integer, ForeignKey('pairing_studies.id'), nullable=False)
    booklet = Column(String(10), nullable=False)          # initial / retest
    source_key = Column(String(100), nullable=False)
    sampled_at = Column(Integer, nullable=False)          # 采样时间，自零点的整分钟
    moisture = Column(String(40), nullable=False)         # 十进制定点字符串，避免浮点
    updated_at = Column(String(40), nullable=False)


class PairingRun(Base):
    __tablename__ = 'pairing_runs'
    id = Column(Integer, primary_key=True)
    pairing_study_id = Column(Integer, ForeignKey('pairing_studies.id'), nullable=False)
    name = Column(String(120), nullable=False)
    status = Column(String(20), default='queued')
    parameters = Column(JSON, nullable=False)
    snapshot = Column(JSON, nullable=False)
    checksum = Column(String(64), nullable=False)
    frozen_revision = Column(Integer, nullable=False)
    result = Column(JSON, nullable=True)
    error = Column(Text, default='')
    created_at = Column(String(40), nullable=False)


class PairingReview(Base):
    __tablename__ = 'pairing_reviews'
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey('pairing_runs.id'), unique=True, nullable=False)
    note = Column(Text, nullable=False)
    author = Column(String(120), nullable=False)
    created_at = Column(String(40), nullable=False)
