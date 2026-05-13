from sqlalchemy import Column, Integer, String, Float, DateTime, Text, Boolean, JSON, ForeignKey
from sqlalchemy.sql import func
from database import Base

class Tariff(Base):
    __tablename__ = "tariffs"
    id = Column(Integer, primary_key=True, index=True)
    service_name = Column(String, unique=True, index=True)
    price = Column(Float)
    unit = Column(String)

class Meeting(Base):
    __tablename__ = "meetings"
    id = Column(Integer, primary_key=True, index=True)
    date = Column(DateTime)
    description = Column(Text, nullable=True)
    agenda_items = Column(JSON, default=list)

class Document(Base):
    __tablename__ = "documents"
    id = Column(Integer, primary_key=True, index=True)
    doc_type = Column(String)
    title = Column(String)
    file_url = Column(String)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

class WorkPlan(Base):
    __tablename__ = "work_plans"
    id = Column(Integer, primary_key=True, index=True)
    task_name = Column(String)
    planned_budget = Column(Float)
    status = Column(String, default="planned")
    completion_percent = Column(Integer, default=0)
    created_at = Column(DateTime, default=func.now())
    start_date = Column(DateTime, nullable=True)
    end_date = Column(DateTime, nullable=True)

class Appeal(Base):
    __tablename__ = "appeals"
    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(String, index=True)
    full_name = Column(String)
    plot_number = Column(String)
    question = Column(Text)
    status = Column(String, default="waiting")
    created_at = Column(DateTime, default=func.now())
    answered_at = Column(DateTime, nullable=True)
    satisfaction_score = Column(Integer, nullable=True)
    board_response = Column(Text, nullable=True)
    reminder_sent = Column(Boolean, default=False)

class AppealFile(Base):
    __tablename__ = "appeal_files"
    id = Column(Integer, primary_key=True, index=True)
    appeal_id = Column(Integer, ForeignKey("appeals.id"))
    file_path = Column(String, nullable=False)
    created_at = Column(DateTime, default=func.now())

class Notification(Base):
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)
    message = Column(Text)
    is_sent = Column(Boolean, default=False)
    created_at = Column(DateTime, default=func.now())

def init_db():
    from database import engine
    Base.metadata.create_all(bind=engine)
