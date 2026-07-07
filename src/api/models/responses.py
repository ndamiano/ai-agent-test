from pydantic import BaseModel
from typing import List

class AgentResponse(BaseModel):
    id: str
    name: str
    description: str
    tools: List[str]
