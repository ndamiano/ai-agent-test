from typing import Optional
from threading import Lock
from tools.project import Project

class ProjectManager:
    """Manages the current project for the agentic workflow"""
    
    _instance = None
    _lock = Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not hasattr(self, 'initialized'):
            self._current_project: Optional[Project] = None
            self.initialized = True
    
    def set_current_project(self, project: Project):
        """Set the current active project"""
        self._current_project = project
        
    def get_current_project(self) -> Optional[Project]:
        """Get the current active project"""
        return self._current_project
    
    def require_current_project(self) -> Project:
        """Get current project or raise if none set"""
        if self._current_project is None:
            raise RuntimeError("No current project set")
        return self._current_project
    
    def clear_current_project(self):
        """Clear the current project"""
        self._current_project = None

# Global instance
project_manager = ProjectManager()