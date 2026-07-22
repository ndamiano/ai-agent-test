from typing import List

from pydantic import BaseModel


class AgentResponse(BaseModel):
    id: str
    name: str
    description: str
    tools: List[str]
