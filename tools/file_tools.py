"""File tools for the AI agent system"""

from pathlib import Path
from typing import Dict, Any
import glob as glob_module

from .logging_utils import log_error
from tools.tool_manager import tool_manager


def _normalize_path(path: str) -> str:
    return "outputs/" + path.removeprefix("outputs/")


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
        path: File path relative to outputs directory (e.g., 'report.txt' or 'data/output.json')
        content: Content to write to the file
        mode: File mode ('w' for write, 'a' for append, etc.)
        encoding: File encoding (default: utf-8)
        create_dirs: Whether to create parent directories if they don't exist

    Returns:
        Dict containing success status and file path
    """
    try:
        path = _normalize_path(path)
        file_path = Path(path)
        
        # Create parent directories if requested
        if create_dirs:
            file_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(file_path, mode=mode, encoding=encoding) as f:
            f.write(content)
        
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


def read_file(
    path: str,
    encoding: str = "utf-8"
) -> Dict[str, Any]:
    """
    Read content from a file in the outputs directory.

    Args:
        path: File path relative to outputs directory (e.g., 'report.txt' or 'data/output.json')
        encoding: File encoding (default: utf-8)

    Returns:
        Dict containing success status, file path, and content
    """
    try:
        path = _normalize_path(path)
        file_path = Path(path)

        if not file_path.exists():
            return {
                "success": False,
                "error": f"File not found: {path}",
                "file_path": str(file_path)
            }

        if not file_path.is_file():
            return {
                "success": False,
                "error": f"Path is not a file: {path}",
                "file_path": str(file_path)
            }

        with open(file_path, mode="r", encoding=encoding) as f:
            content = f.read()

        result = {
            "success": True,
            "file_path": str(file_path),
            "content": content,
            "content_length": len(content)
        }

        return result

    except Exception as e:
        error_msg = f"Failed to read file {path}: {str(e)}"
        log_error("ReadFileError", error_msg, {
            "path": path,
            "encoding": encoding,
            "error": str(e)
        })

        return {
            "success": False,
            "error": error_msg,
            "file_path": path
        }


def list_files(
    path: str = "",
    pattern: str = "*",
    recursive: bool = False
) -> Dict[str, Any]:
    """
    List files in the outputs directory.

    Args:
        path: Directory path relative to outputs directory (default: root of outputs)
        pattern: Glob pattern to match files (default: "*" for all files)
        recursive: Whether to search recursively (default: False)

    Returns:
        Dict containing success status and list of file paths
    """
    try:
        base_path = _normalize_path(path)

        dir_path = Path(base_path)

        if not dir_path.exists():
            dir_path.mkdir(parents=True, exist_ok=True)
            return {
                "success": True,
                "directory": str(dir_path),
                "files": [],
                "count": 0
            }

        # Build glob pattern
        if recursive:
            glob_pattern = f"{dir_path}/**/{pattern}"
            matches = glob_module.glob(glob_pattern, recursive=True)
        else:
            glob_pattern = f"{dir_path}/{pattern}"
            matches = glob_module.glob(glob_pattern)

        # Filter to only files (not directories)
        files = [str(Path(f).relative_to(dir_path)) for f in matches if Path(f).is_file()]
        files.sort()

        result = {
            "success": True,
            "directory": str(dir_path),
            "files": files,
            "count": len(files),
            "pattern": pattern,
            "recursive": recursive
        }

        return result

    except Exception as e:
        error_msg = f"Failed to list files in {path}: {str(e)}"
        log_error("ListFilesError", error_msg, {
            "path": path,
            "pattern": pattern,
            "recursive": recursive,
            "error": str(e)
        })

        return {
            "success": False,
            "error": error_msg,
            "directory": path
        }


def delete_file(
    path: str
) -> Dict[str, Any]:
    """
    Delete a file from the outputs directory.

    Args:
        path: File path relative to outputs directory (e.g., 'report.txt' or 'data/output.json')

    Returns:
        Dict containing success status and file path
    """
    try:
        path = _normalize_path(path)
        file_path = Path(path)

        # Safety check: ensure we're only deleting from outputs directory
        if not str(file_path.resolve()).startswith(str(Path("outputs").resolve())):
            return {
                "success": False,
                "error": "Can only delete files from outputs directory",
                "file_path": str(file_path)
            }

        if not file_path.exists():
            return {
                "success": False,
                "error": f"File not found: {path}",
                "file_path": str(file_path)
            }

        if not file_path.is_file():
            return {
                "success": False,
                "error": f"Path is not a file: {path}",
                "file_path": str(file_path)
            }

        # Delete the file
        file_path.unlink()

        result = {
            "success": True,
            "file_path": str(file_path),
            "message": f"File deleted successfully: {file_path}"
        }

        return result

    except Exception as e:
        error_msg = f"Failed to delete file {path}: {str(e)}"
        log_error("DeleteFileError", error_msg, {
            "path": path,
            "error": str(e)
        })

        return {
            "success": False,
            "error": error_msg,
            "file_path": path
        }


def register_file_tools():
    """Register file tools with the tool manager"""

    # Register write_to_file tool
    tool_manager.register_tool(
        name="write_to_file",
        description=(
            "Write content to a file in the outputs directory. "
            "Files are written to outputs/{path}."
        ),
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
        fn=write_to_file,
        auto_inject_context=False
    )

    # Register read_file tool
    tool_manager.register_tool(
        name="read_file",
        description=(
            "Read content from a file in the outputs directory. "
            "Reads from outputs/{path}."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to outputs directory (e.g., 'report.txt' or 'data/output.json')"
                },
                "encoding": {
                    "type": "string",
                    "default": "utf-8",
                    "description": "File encoding (default: utf-8)"
                }
            },
            "required": ["path"]
        },
        fn=read_file,
        auto_inject_context=False
    )

    # Register list_files tool
    tool_manager.register_tool(
        name="list_files",
        description=(
            "List files in the outputs directory. "
            "Lists files in outputs/{path}. "
            "Supports glob patterns and recursive search."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "default": "",
                    "description": "Directory path relative to outputs directory (default: root of outputs)"
                },
                "pattern": {
                    "type": "string",
                    "default": "*",
                    "description": "Glob pattern to match files (e.g., '*.txt', '*.json', default: '*' for all files)"
                },
                "recursive": {
                    "type": "boolean",
                    "default": False,
                    "description": "Whether to search recursively in subdirectories (default: False)"
                }
            },
            "required": []
        },
        fn=list_files,
        auto_inject_context=False
    )

    # Register delete_file tool
    tool_manager.register_tool(
        name="delete_file",
        description=(
            "Delete a file from the outputs directory. "
            "Deletes from outputs/{path}. "
            "Safety: Can only delete files from the outputs directory."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to outputs directory (e.g., 'report.txt' or 'data/output.json')"
                }
            },
            "required": ["path"]
        },
        fn=delete_file,
        auto_inject_context=False
    )
