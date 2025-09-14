"""Story-level data classes"""

from dataclasses import dataclass
from typing import List, Optional

@dataclass
class StoryContext:
    title: str
    themes: List[str]
    tone: str
    genre: str
    target_audience: str = "mature"