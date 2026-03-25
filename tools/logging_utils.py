"""Logging utilities for tracking tool calls and agent activity"""

import json
import datetime
import os
import logging
import threading
from typing import Any, Dict, Optional


class ToolLogger:
    """Handles logging of tool calls and agent activity"""
    
    def __init__(self, log_dir: str = "logs"):
        self.log_dir = log_dir
        self.ensure_log_dir()
        self._lock = threading.Lock()
        
        # Set up proper Python logging with fixed logger name
        self._setup_logging()
        
        # Create a file handler for JSONL logs
        self._jsonl_handler = logging.FileHandler(
            os.path.join(self.log_dir, f"tool_logger.jsonl"),
            encoding='utf-8'
        )
        self._jsonl_handler.setFormatter(logging.Formatter('%(message)s'))
    
    def ensure_log_dir(self):
        """Ensure the log directory exists"""
        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir)
    
    def _generate_session_id(self) -> str:
        """Generate a unique session ID"""
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"session_{timestamp}"
    
    def _setup_logging(self):
        """Set up Python logging configuration"""
        # Create logger with fixed name
        self.logger = logging.getLogger("tool_logger")
        self.logger.setLevel(logging.DEBUG)
        
        # Create console handler only if not already present
        if not self.logger.handlers:
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.INFO)
            
            # Create formatter
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            console_handler.setFormatter(formatter)
            
            # Add handlers to logger
            self.logger.addHandler(console_handler)
    
    def log_tool_call(self, tool_name: str, parameters: Dict[str, Any], result: Any, error: Optional[str] = None):
        """Log a tool call with all relevant information"""
        log_entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "type": "tool_call",
            "tool_name": tool_name,
            "parameters": parameters,
            "result": str(result) if result is not None else None,
            "error": error,
            "success": error is None
        }
        
        self._write_log(log_entry)
        
        # Log to console using proper logging
        if error:
            self.logger.error(f"Tool '{tool_name}' failed: {error}")
        else:
            self.logger.info(f"Tool '{tool_name}' executed successfully")
            self.logger.debug(f"Parameters: {parameters}")
            self.logger.debug(f"Result: {result}")
    
    def log_agent_decision(self, user_input: str, decision: str, details: Optional[Dict[str, Any]] = None):
        """Log agent decisions and reasoning"""
        log_entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "type": "agent_decision",
            "user_input": user_input,
            "decision": decision,
            "details": details or {}
        }
        
        self._write_log(log_entry)
        
        # Log to console
        self.logger.info(f"Agent decision: {decision}")
        if details:
            self.logger.debug(f"Details: {details}")
    
    def log_error(self, error_type: str, error_message: str, context: Optional[Dict[str, Any]] = None):
        """Log general errors"""
        log_entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "type": "error",
            "error_type": error_type,
            "error_message": error_message,
            "context": context or {}
        }
        
        self._write_log(log_entry)
        
        # Log to console
        self.logger.error(f"Error ({error_type}): {error_message}")
        if context:
            self.logger.debug(f"Context: {context}")
    
    def error(self, message: str, context: Optional[Dict[str, Any]] = None):
        """Convenience method for logging errors (compatible with standard logging interface)"""
        self.log_error("error", message, context)
    
    def warning(self, message: str, context: Optional[Dict[str, Any]] = None):
        """Convenience method for logging warnings (compatible with standard logging interface)"""
        self.log_error("warning", message, context)
    
    def info(self, message: str, context: Optional[Dict[str, Any]] = None):
        """Convenience method for logging info (compatible with standard logging interface)"""
        log_entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "type": "info",
            "message": message,
            "context": context or {}
        }
        
        self._write_log(log_entry)
        
        # Log to console
        self.logger.info(f"Info: {message}")
        if context:
            self.logger.debug(f"Context: {context}")
    
    def log_llm_interaction(
        self,
        connector: str,
        model: str,
        prompt: Any,
        response: Any,
        task_id: Optional[str] = None,
        subtask_id: Optional[str] = None,
        error: Optional[str] = None
    ):
        """Log an LLM interaction with prompt and response"""
        log_entry = {
            "timestamp": datetime.datetime.now().isoformat(),
            "type": "llm_interaction",
            "connector": connector,
            "model": model,
            "prompt": prompt,
            "response": str(response) if response is not None else None,
            "task_id": task_id,
            "subtask_id": subtask_id,
            "error": error,
            "success": error is None
        }
        
        self._write_log(log_entry)
        
        # Log summary to console
        if error:
            self.logger.error(f"LLM call to {connector} ({model}) failed: {error}")
        else:
            self.logger.debug(f"LLM call to {connector} ({model}) completed")

    def _write_log(self, log_entry: Dict[str, Any]):
        """Write a log entry to the session log file"""
        with self._lock:
            try:
                # Write to JSONL file
                log_file = os.path.join(self.log_dir, "tool_logger.jsonl")
                with open(log_file, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
            except Exception as e:
                self.logger.error(f"Failed to write log entry: {e}")
    
    def get_session_log_path(self) -> str:
        """Get the path to the current session log file"""
        return os.path.join(self.log_dir, "tool_logger.jsonl")


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


def log_llm_interaction(
    connector: str,
    model: str,
    prompt: Any,
    response: Any,
    task_id: Optional[str] = None,
    subtask_id: Optional[str] = None,
    error: Optional[str] = None
):
    """Convenience function to log LLM interactions"""
    tool_logger.log_llm_interaction(
        connector=connector,
        model=model,
        prompt=prompt,
        response=response,
        task_id=task_id,
        subtask_id=subtask_id,
        error=error
    )
