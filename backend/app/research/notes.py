from __future__ import annotations
import uuid
from datetime import datetime,timezone
from pydantic import BaseModel,ConfigDict,Field
class ReportNote(BaseModel):
    model_config=ConfigDict(extra="forbid")
    id:str; report_id:str; content:str=Field(min_length=1,max_length=5000); created_at:datetime; updated_at:datetime
class NoteStore:
    def __init__(self,repository):self._repository=repository
    def create(self,report_id:str,content:str)->ReportNote:
        now=datetime.now(timezone.utc); item=ReportNote(id=uuid.uuid4().hex,report_id=report_id,content=content,created_at=now,updated_at=now);self._repository.insert_note(item);return item
    def update(self,note_id:str,content:str)->ReportNote|None:return self._repository.update_note(note_id,content)
