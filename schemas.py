from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class TariffBase(BaseModel):
    service_name: str
    price: float
    unit: str

class MeetingBase(BaseModel):
    date: datetime
    description: Optional[str] = None

class DocumentBase(BaseModel):
    doc_type: str
    title: str
    file_url: str

class WorkPlanBase(BaseModel):
    task_name: str
    planned_budget: float
    status: str = "planned"
    completion_percent: int = 0

class AppealCreate(BaseModel):
    full_name: str
    plot_number: str
    question: str

class AppealResponse(BaseModel):
    id: int
    full_name: str
    plot_number: str
    question: str
    status: str
    created_at: datetime
    answered_at: Optional[datetime]
    satisfaction_score: Optional[int]
    board_response: Optional[str]

class NotificationCreate(BaseModel):
    title: str
    message: str
