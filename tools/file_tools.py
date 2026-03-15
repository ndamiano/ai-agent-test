"""Write to file tool for the AI agent system"""

import os
from pathlib import Path
from typing import Dict, Any, Optional
import json

from .logging_utils import log_error
from tools.tool_manager import tool_manager


def write_to_file(
    path: str,
    content: str,
    mode: str = "w",
    encoding: str = "utf-8",
    create_dirs: bool = True,
    task_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Write content to a file in the outputs directory, automatically organized by task_id.

    Files are written to outputs/{task_id}/{path} when a task_id is available,
    otherwise falls back to outputs/{path}.

    Args:
        path: File path relative to outputs directory
        content: Content to write to the file
        mode: File mode ('w' for write, 'a' for append, etc.)
        encoding: File encoding (default: utf-8)
        create_dirs: Whether to create parent directories if they don't exist
        task_id: Task ID (auto-injected from execution context if not provided)

    Returns:
        Dict containing success status and file path
    """
    try:

        # Ensure path is relative to outputs directory, organized by task_id
        if not path.startswith("outputs/"):
            if task_id:
                path = f"outputs/{task_id}/{path}"
            else:
                path = f"outputs/{path}"

        # Convert to Path object for better path handling
        file_path = Path(path)
        
        # Create parent directories if requested
        if create_dirs:
            file_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write content to file
        with open(file_path, mode=mode, encoding=encoding) as f:
            f.write(content)
        
        # Log successful operation
        result = {
            "success": True,
            "file_path": str(file_path),
            "mode": mode,
            "content_length": len(content)
        }
        
        return result
        
    except Exception as e:
        error_msg = f"Failed to write to file {path}: {str(e)}"
        log_error("WriteToFileError", error_msg, {
            "path": path,
            "mode": mode,
            "encoding": encoding,
            "error": str(e)
        })
        
        return {
            "success": False,
            "error": error_msg,
            "file_path": path
        }


def register_file_tools():
    """Register write tools with the tool manager"""
    
    # Register write_to_file tool
    tool_manager.register_tool(
        name="write_to_file",
        description=(
            "Write content to a file in the outputs directory. "
            "Files are automatically organized by task_id when available: outputs/{task_id}/{path}"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to task outputs directory (e.g., 'report.txt' or 'data/output.json')"
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file"
                },
                "mode": {
                    "type": "string",
                    "enum": ["w", "a", "x"],
                    "default": "w",
                    "description": "File mode: 'w' for write (overwrite), 'a' for append, 'x' for exclusive creation"
                },
                "encoding": {
                    "type": "string",
                    "default": "utf-8",
                    "description": "File encoding (default: utf-8)"
                },
                "create_dirs": {
                    "type": "boolean",
                    "default": True,
                    "description": "Whether to create parent directories if they don't exist (default: True)"
                }
            },
            "required": ["path", "content"]
        },
        fn=write_to_file,
        auto_inject_context=True
    )