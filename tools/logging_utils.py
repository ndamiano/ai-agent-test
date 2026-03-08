"""Logging utilities for tracking tool calls and agent activity"""

import json
import datetime
import os
from typing import Any, Dict, Optional


class ToolLogger:
    """Handles logging of tool calls and agent activity"""
    
    def __init__(self, log_dir: str = "logs"):
        self.log_dir = log_dir
        self.ensure_log_dir()
        self.session_id = self._generate_session_id()
        
    def ensure_log_dir(self):
        """Ensure the log directory exists"""
        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir)
    
    def _generate_session_id(self) -> str:
        """Generate a unique session ID"""
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"session_{timestamp}"
    
    def log_tool_call(self, tool_name: str, parameters: Dict[str, Any], result: Any, error: Optional[str] = None):
        """Log a tool call with all relevant information"""
        log_entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "session_id": self.session_id,
            "type": "tool_call",
            "tool_name": tool_name,
            "parameters": parameters,
            "result": str(result) if result is not None else None,
            "error": error,
            "success": error is None
        }
        
        self._write_log(log_entry)
        
        # Also print to console for immediate feedback
        if error:
            print(f"❌ Tool '{tool_name}' failed: {error}")
        else:
            print(f"✅ Tool '{tool_name}' executed successfully")
            print(f"   Parameters: {parameters}")
            print(f"   Result: {result}")
    
    def log_agent_decision(self, user_input: str, decision: str, details: Optional[Dict[str, Any]] = None):
        """Log agent decisions and reasoning"""
        log_entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "session_id": self.session_id,
            "type": "agent_decision",
            "user_input": user_input,
            "decision": decision,
            "details": details or {}
        }
        
        self._write_log(log_entry)
        
        # Print to console
        print(f"🤖 Agent decision: {decision}")
        if details:
            print(f"   Details: {details}")
    
    def log_error(self, error_type: str, error_message: str, context: Optional[Dict[str, Any]] = None):
        """Log general errors"""
        log_entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "session_id": self.session_id,
            "type": "error",
            "error_type": error_type,
            "error_message": error_message,
            "context": context or {}
        }
        
        self._write_log(log_entry)
        
        # Print to console
        print(f"🚨 Error ({error_type}): {error_message}")
        if context:
            print(f"   Context: {context}")
    
    def _write_log(self, log_entry: Dict[str, Any]):
        """Write a log entry to the session log file"""
        log_file = os.path.join(self.log_dir, f"{self.session_id}.jsonl")
        
        try:
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
        except Exception as e:
            print(f"Failed to write log entry: {e}")
    
    def get_session_log_path(self) -> str:
        """Get the path to the current session log file"""
        return os.path.join(self.log_dir, f"{self.session_id}.jsonl")


# Global logger instance
tool_logger = ToolLogger()


def log_tool_call(tool_name: str, parameters: Dict[str, Any], result: Any, error: Optional[str] = None):
    """Convenience function to log tool calls"""
    tool_logger.log_tool_call(tool_name, parameters, result, error)


def log_agent_decision(user_input: str, decision: str, details: Optional[Dict[str, Any]] = None):
    """Convenience function to log agent decisions"""
    tool_logger.log_agent_decision(user_input, decision, details)


def log_error(error_type: str, error_message: str, context: Optional[Dict[str, Any]] = None):
    """Convenience function to log errors"""
    tool_logger.log_error(error_type, error_message, context)