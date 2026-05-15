from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship

from src.db.database import Base


class SessionModel(Base):
    __tablename__ = "sessions"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(Text, default="")
    tags = Column(Text, default="")
    status = Column(String, nullable=False, default="created")
    started_at = Column(DateTime, nullable=True)
    stopped_at = Column(DateTime, nullable=True)
    duration_ms = Column(Integer, default=0)
    operation_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    operations = relationship("OperationModel", back_populates="session", cascade="all, delete-orphan")


class OperationModel(Base):
    __tablename__ = "operations"
    __table_args__ = (Index("idx_operations_session", "session_id", "seq_num"),)

    id = Column(String, primary_key=True)
    session_id = Column(String, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    seq_num = Column(Integer, nullable=False)
    timestamp = Column(Integer, nullable=False)
    type = Column(String, nullable=False)
    data = Column(Text, nullable=False)
    context = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))

    session = relationship("SessionModel", back_populates="operations")


class AutomationModel(Base):
    __tablename__ = "automations"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(Text, default="")
    source_session_id = Column(String, ForeignKey("sessions.id"), nullable=True)
    source_pattern = Column(Text, nullable=True)
    flow_definition = Column(Text, nullable=False)
    confidence = Column(Float, default=0.0)
    status = Column(String, nullable=False, default="draft")
    execution_count = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
    updated_at = Column(DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    executions = relationship("ExecutionModel", back_populates="automation", cascade="all, delete-orphan")


class ExecutionModel(Base):
    __tablename__ = "executions"

    id = Column(String, primary_key=True)
    automation_id = Column(String, ForeignKey("automations.id"), nullable=False)
    status = Column(String, nullable=False, default="pending")
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    total_steps = Column(Integer, default=0)
    completed_steps = Column(Integer, default=0)
    failed_steps = Column(Integer, default=0)
    error_summary = Column(Text, nullable=True)
    variables = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))

    automation = relationship("AutomationModel", back_populates="executions")
    steps = relationship("ExecutionStepModel", back_populates="execution", cascade="all, delete-orphan")


class ExecutionStepModel(Base):
    __tablename__ = "execution_steps"
    __table_args__ = (Index("idx_exec_steps", "execution_id", "step_index"),)

    id = Column(String, primary_key=True)
    execution_id = Column(String, ForeignKey("executions.id", ondelete="CASCADE"), nullable=False)
    step_id = Column(String, nullable=False)
    step_type = Column(String, nullable=False)
    step_index = Column(Integer, nullable=False)
    status = Column(String, nullable=False, default="pending")
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    screenshot_before = Column(Text, nullable=True)
    screenshot_after = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))

    execution = relationship("ExecutionModel", back_populates="steps")


class DesktopSnapshotModel(Base):
    __tablename__ = "desktop_snapshots"

    id = Column(String, primary_key=True)
    timestamp = Column(Integer, nullable=False)
    active_window_title = Column(String, nullable=True)
    active_window_app = Column(String, nullable=True)
    active_window_class = Column(String, nullable=True)
    active_window_process = Column(String, nullable=True)
    active_window_bounds = Column(Text, nullable=True)
    window_count = Column(Integer, default=0)
    window_list = Column(Text, nullable=True)
    screenshot_path = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC))
