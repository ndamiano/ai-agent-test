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
    create_dirs: bool = True
) -> Dict[str, Any]:
    """
    Write content to a file in the outputs directory.
    
    Args:
        path: File path relative to outputs directory
        content: Content to write to the file
        mode: File mode ('w' for write, 'a' for append, etc.)
        encoding: File encoding (default: utf-8)
        create_dirs: Whether to create parent directories if they don't exist
    
    Returns:
        Dict containing success status and file path
    """
    try:
        # Ensure path is relative to outputs directory
        if not path.startswith("outputs/"):
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


def append_to_file(
    path: str,
    content: str,
    encoding: str = "utf-8",
    create_dirs: bool = True
) -> Dict[str, Any]:
    """
    Append content to an existing file in the outputs directory.
    
    Args:
        path: File path relative to outputs directory
        content: Content to append to the file
        encoding: File encoding (default: utf-8)
        create_dirs: Whether to create parent directories if they don't exist
    
    Returns:
        Dict containing success status and file path
    """
    return write_to_file(path, content, mode="a", encoding=encoding, create_dirs=create_dirs)


def write_json_to_file(
    path: str,
    data: Any,
    indent: int = 2,
    encoding: str = "utf-8",
    create_dirs: bool = True
) -> Dict[str, Any]:
    """
    Write JSON data to a file in the outputs directory.
    
    Args:
        path: File path relative to outputs directory
        data: JSON-serializable data to write
        indent: JSON indentation level (default: 2)
        encoding: File encoding (default: utf-8)
        create_dirs: Whether to create parent directories if they don't exist
    
    Returns:
        Dict containing success status and file path
    """
    try:
        # Convert data to JSON string
        json_content = json.dumps(data, indent=indent, ensure_ascii=False)
        
        # Use the main write_to_file function
        result = write_to_file(path, json_content, mode="w", encoding=encoding, create_dirs=create_dirs)
        
        # Add JSON-specific info to result
        result["json_data"] = data
        
        return result
        
    except json.JSONDecodeError as e:
        error_msg = f"Failed to serialize data to JSON for file {path}: {str(e)}"
        log_error("JSONSerializationError", error_msg, {
            "path": path,
            "data_type": type(data).__name__,
            "error": str(e)
        })
        
        return {
            "success": False,
            "error": error_msg,
            "file_path": path
        }
    except Exception as e:
        error_msg = f"Failed to write JSON to file {path}: {str(e)}"
        log_error("WriteJSONToFileError", error_msg, {
            "path": path,
            "data_type": type(data).__name__,
            "error": str(e)
        })
        
        return {
            "success": False,
            "error": error_msg,
            "file_path": path
        }


# Tool registration for the agent system
def register_write_tools():
    """Register write tools with the tool manager"""
    
    # Register write_to_file tool
    tool_manager.register_tool(
        name="write_to_file",
        description="Write content to a file in the outputs directory",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to outputs directory (e.g., 'report.txt' or 'data/output.json')"
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
        fn=write_to_file
    )
    
    # Register append_to_file tool
    tool_manager.register_tool(
        name="append_to_file",
        description="Append content to an existing file in the outputs directory",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to outputs directory"
                },
                "content": {
                    "type": "string",
                    "description": "Content to append to the file"
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
        fn=append_to_file
    )
    
    # Register write_json_to_file tool
    tool_manager.register_tool(
        name="write_json_to_file",
        description="Write JSON data to a file in the outputs directory",
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to outputs directory"
                },
                "data": {
                    "type": "any",
                    "description": "JSON-serializable data to write"
                },
                "indent": {
                    "type": "integer",
                    "default": 2,
                    "description": "JSON indentation level (default: 2)"
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
            "required": ["path", "data"]
        },
        fn=write_json_to_file
    )