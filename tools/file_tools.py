"""File tools for reading, writing, editing, and searching files"""

from pathlib import Path
from typing import Dict, Any, Optional
import logging
import subprocess

logger = logging.getLogger(__name__)
from tools.tool_manager import tool_manager


def read_file(
    file_path: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Read a file from the filesystem.

    When executed within a task context with a working directory, relative paths
    are automatically scoped to the working directory.
    Absolute paths are never modified.

    Args:
        file_path: Path to the file (absolute or relative)
        start_line: Optional starting line number (1-indexed)
        end_line: Optional ending line number (1-indexed, inclusive)

    Returns:
        Dict containing success status, file path, and content
    """
    try:
        from tools.execution_context import resolve_base_path
        path = resolve_base_path(file_path)

        if not path.exists():
            return {
                "success": False,
                "error": f"File not found: {file_path}",
                "file_path": str(path)
            }

        if not path.is_file():
            return {
                "success": False,
                "error": f"Path is not a file: {file_path}",
                "file_path": str(path)
            }

        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            if start_line is not None or end_line is not None:
                lines = f.readlines()
                start = (start_line - 1) if start_line else 0
                end = end_line if end_line else len(lines)
                content = ''.join(lines[start:end])
            else:
                content = f.read()

        result = {
            "success": True,
            "file_path": str(path),
            "content": content,
            "content_length": len(content),
        }

        if start_line is not None or end_line is not None:
            result["start_line"] = start_line or 1
            result["end_line"] = end_line or len(content.splitlines())

        return result

    except Exception as e:
        error_msg = f"Failed to read file {file_path}: {str(e)}"
        logger.error(error_msg)
        return {
            "success": False,
            "error": error_msg,
            "file_path": file_path
        }


def write_to_file(
    file_path: str,
    content: str,
    create_dirs: bool = True,
) -> Dict[str, Any]:
    """
    Write content to a file, creating it if it doesn't exist.

    When executed within a task context with a working directory, relative paths
    are automatically scoped to the working directory.
    Absolute paths are never modified.

    Args:
        file_path: Path to the file (absolute or relative)
        content: Content to write to the file
        create_dirs: Whether to create parent directories if they don't exist

    Returns:
        Dict containing success status and file path
    """
    try:
        # Scope relative paths to working directory when in execution context
        from tools.execution_context import get_working_directory
        working_dir = get_working_directory()

        path = Path(file_path).expanduser()

        # If we have a working directory and the path is relative, scope it
        if working_dir and not path.is_absolute():
            path = Path(working_dir) / path

        path = path.resolve()

        # Create parent directories if requested
        if create_dirs:
            path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)

        result = {
            "success": True,
            "file_path": str(path),
            "content_length": len(content)
        }

        return result

    except Exception as e:
        error_msg = f"Failed to write to file {file_path}: {str(e)}"
        logger.error(error_msg)
        return {
            "success": False,
            "error": error_msg,
            "file_path": file_path
        }


def edit_file(
    file_path: str,
    old_text: str,
    new_text: str,
) -> Dict[str, Any]:
    """
    Edit a file by replacing old_text with new_text.

    This is safer than rewriting entire files - just specify what to change.

    When executed within a task context with a working directory, relative paths
    are automatically scoped to the working directory.
    Absolute paths are never modified.

    Args:
        file_path: Path to the file (absolute or relative)
        old_text: Text to find and replace (must match exactly)
        new_text: Text to replace it with

    Returns:
        Dict containing success status and details
    """
    try:
        # Scope relative paths to working directory when in execution context
        from tools.execution_context import get_working_directory
        working_dir = get_working_directory()

        path = Path(file_path).expanduser()

        # If we have a working directory and the path is relative, scope it
        if working_dir and not path.is_absolute():
            path = Path(working_dir) / path

        path = path.resolve()

        if not path.exists():
            return {
                "success": False,
                "error": f"File not found: {file_path}",
                "file_path": str(path)
            }

        # Read current content
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        # Check if old_text exists
        if old_text not in content:
            return {
                "success": False,
                "error": f"Text to replace not found in file",
                "file_path": str(path),
                "old_text": old_text[:100] + "..." if len(old_text) > 100 else old_text
            }

        # Count occurrences
        occurrences = content.count(old_text)
        if occurrences > 1:
            return {
                "success": False,
                "error": f"Text appears {occurrences} times in file. old_text must be unique for safety.",
                "file_path": str(path),
                "occurrences": occurrences
            }

        # Perform replacement
        new_content = content.replace(old_text, new_text)

        # Write back
        with open(path, 'w', encoding='utf-8') as f:
            f.write(new_content)

        return {
            "success": True,
            "file_path": str(path),
            "old_length": len(content),
            "new_length": len(new_content),
            "characters_changed": len(new_text) - len(old_text)
        }

    except Exception as e:
        error_msg = f"Failed to edit file {file_path}: {str(e)}"
        logger.error(error_msg)
        return {
            "success": False,
            "error": error_msg,
            "file_path": file_path
        }


def list_directory(
    directory: str = ".",
    pattern: Optional[str] = None,
    recursive: bool = False,
    max_depth: int = 3,
) -> Dict[str, Any]:
    """
    List files and directories.

    When executed within a task context with a working directory, relative paths
    are automatically scoped to the working directory.
    Absolute paths are never modified.

    Args:
        directory: Directory path to list (default: current directory)
        pattern: Optional glob pattern to filter results (e.g., "*.py", "**/*.js")
        recursive: Whether to search recursively
        max_depth: Maximum depth for recursive search (default: 3)

    Returns:
        Dict containing success status and list of files/directories
    """
    try:
        # Scope relative paths to working directory when in execution context
        from tools.execution_context import get_working_directory
        working_dir = get_working_directory()

        path = Path(directory).expanduser()

        # If we have a working directory and the path is relative, scope it
        if working_dir and not path.is_absolute():
            path = Path(working_dir) / path

        path = path.resolve()

        if not path.exists():
            return {
                "success": False,
                "error": f"Directory not found: {directory}",
                "directory": str(path)
            }

        if not path.is_dir():
            return {
                "success": False,
                "error": f"Path is not a directory: {directory}",
                "directory": str(path)
            }

        files = []
        dirs = []

        if pattern and recursive:
            # Use glob with pattern
            matches = path.glob(pattern)
            for item in matches:
                rel_path = str(item.relative_to(path))
                # Check depth
                depth = len(Path(rel_path).parts)
                if depth <= max_depth:
                    if item.is_file():
                        files.append(rel_path)
                    elif item.is_dir():
                        dirs.append(rel_path)
        elif pattern:
            # Non-recursive glob
            matches = path.glob(pattern)
            for item in matches:
                rel_path = str(item.relative_to(path))
                if item.is_file():
                    files.append(rel_path)
                elif item.is_dir():
                    dirs.append(rel_path)
        elif recursive:
            # Recursive without pattern
            for item in path.rglob('*'):
                rel_path = str(item.relative_to(path))
                depth = len(Path(rel_path).parts)
                if depth <= max_depth:
                    if item.is_file():
                        files.append(rel_path)
                    elif item.is_dir():
                        dirs.append(rel_path)
        else:
            # Just list immediate children
            for item in path.iterdir():
                if item.is_file():
                    files.append(item.name)
                elif item.is_dir():
                    dirs.append(item.name)

        files.sort()
        dirs.sort()

        result = {
            "success": True,
            "directory": str(path),
            "files": files,
            "directories": dirs,
            "file_count": len(files),
            "directory_count": len(dirs),
        }

        return result

    except Exception as e:
        error_msg = f"Failed to list directory {directory}: {str(e)}"
        logger.error(error_msg)
        return {
            "success": False,
            "error": error_msg,
            "directory": directory
        }


def grep_files(
    pattern: str,
    directory: str = ".",
    file_pattern: Optional[str] = None,
    ignore_case: bool = False,
    max_results: int = 100,
) -> Dict[str, Any]:
    """
    Search for a pattern in files using grep.

    When executed within a task context with a working directory, relative paths
    are automatically scoped to the working directory.
    Absolute paths are never modified.

    Args:
        pattern: Regular expression pattern to search for
        directory: Directory to search in (default: current directory)
        file_pattern: Optional file glob pattern (e.g., "*.py")
        ignore_case: Whether to ignore case in search
        max_results: Maximum number of results to return

    Returns:
        Dict containing success status and search results
    """
    try:
        # Scope relative paths to working directory when in execution context
        from tools.execution_context import get_working_directory
        working_dir = get_working_directory()

        path = Path(directory).expanduser()

        # If we have a working directory and the path is relative, scope it
        if working_dir and not path.is_absolute():
            path = Path(working_dir) / path

        path = path.resolve()

        if not path.exists():
            return {
                "success": False,
                "error": f"Directory not found: {directory}",
                "directory": str(path)
            }

        # Build grep command
        cmd = ["grep", "-rn"]
        if ignore_case:
            cmd.append("-i")

        cmd.append(pattern)
        cmd.append(str(path))

        if file_pattern:
            cmd.extend(["--include", file_pattern])

        # Execute grep
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )

        # Parse results
        matches = []
        if result.stdout:
            lines = result.stdout.strip().split('\n')
            for line in lines[:max_results]:
                # Format: file_path:line_number:content
                parts = line.split(':', 2)
                if len(parts) >= 3:
                    matches.append({
                        "file": parts[0],
                        "line": int(parts[1]),
                        "content": parts[2].strip()
                    })

        return {
            "success": True,
            "pattern": pattern,
            "directory": str(path),
            "matches": matches,
            "match_count": len(matches),
            "truncated": len(result.stdout.strip().split('\n')) > max_results if result.stdout else False
        }

    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": "Search timed out after 30 seconds",
            "pattern": pattern
        }
    except Exception as e:
        error_msg = f"Failed to grep for pattern '{pattern}': {str(e)}"
        logger.error(error_msg)
        return {
            "success": False,
            "error": error_msg,
            "pattern": pattern
        }


def delete_file(
    file_path: str
) -> Dict[str, Any]:
    """
    Delete a file from the filesystem.

    When executed within a task context with a working directory, relative paths
    are automatically scoped to the working directory.
    Absolute paths are never modified.

    Args:
        file_path: Path to the file (absolute or relative)

    Returns:
        Dict containing success status and file path
    """
    try:
        # Scope relative paths to working directory when in execution context
        from tools.execution_context import get_working_directory
        working_dir = get_working_directory()

        path = Path(file_path).expanduser()

        # If we have a working directory and the path is relative, scope it
        if working_dir and not path.is_absolute():
            path = Path(working_dir) / path

        path = path.resolve()

        if not path.exists():
            return {
                "success": False,
                "error": f"File not found: {file_path}",
                "file_path": str(path)
            }

        if not path.is_file():
            return {
                "success": False,
                "error": f"Path is not a file: {file_path}",
                "file_path": str(path)
            }

        # Delete the file
        path.unlink()

        result = {
            "success": True,
            "file_path": str(path),
            "message": f"File deleted successfully: {path}"
        }

        return result

    except Exception as e:
        error_msg = f"Failed to delete file {file_path}: {str(e)}"
        logger.error(error_msg)
        return {
            "success": False,
            "error": error_msg,
            "file_path": file_path
        }


def register_file_tools():
    """Register file tools with the tool manager"""

    tool_manager.register_tool(
        name="read_file",
        description=(
            "Read a file from the filesystem. "
            "Can optionally read specific line ranges. "
            "Works with any file type - code, text, config, etc."
        ),
        parameters={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file (absolute or relative)"
                },
                "start_line": {
                    "type": "integer",
                    "description": "Optional starting line number (1-indexed)"
                },
                "end_line": {
                    "type": "integer",
                    "description": "Optional ending line number (1-indexed, inclusive)"
                }
            },
            "required": ["file_path"]
        },
        fn=read_file,
        auto_inject_context=False
    )

    tool_manager.register_tool(
        name="write_to_file",
        description=(
            "Write content to a file, creating it if it doesn't exist. "
            "Creates parent directories automatically. "
            "Use this for creating new files or completely replacing file contents."
        ),
        parameters={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file (absolute or relative)"
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file"
                },
                "create_dirs": {
                    "type": "boolean",
                    "default": True,
                    "description": "Whether to create parent directories if they don't exist"
                }
            },
            "required": ["file_path", "content"]
        },
        fn=write_to_file,
        auto_inject_context=False
    )

    tool_manager.register_tool(
        name="edit_file",
        description=(
            "Edit a file by replacing specific text. "
            "Safer than rewriting entire files - just specify what to change. "
            "The old_text must appear exactly once in the file for safety."
        ),
        parameters={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file (absolute or relative)"
                },
                "old_text": {
                    "type": "string",
                    "description": "Text to find and replace (must match exactly and appear only once)"
                },
                "new_text": {
                    "type": "string",
                    "description": "Text to replace it with"
                }
            },
            "required": ["file_path", "old_text", "new_text"]
        },
        fn=edit_file,
        auto_inject_context=False
    )

    tool_manager.register_tool(
        name="list_directory",
        description=(
            "List files and directories. "
            "Supports glob patterns and recursive listing. "
            "Use this to explore file structure."
        ),
        parameters={
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "default": ".",
                    "description": "Directory path (default: current directory)"
                },
                "pattern": {
                    "type": "string",
                    "description": "Optional glob pattern (e.g., '*.py', '**/*.js')"
                },
                "recursive": {
                    "type": "boolean",
                    "default": False,
                    "description": "Whether to search recursively"
                },
                "max_depth": {
                    "type": "integer",
                    "default": 3,
                    "description": "Maximum depth for recursive search (default: 3)"
                }
            },
            "required": []
        },
        fn=list_directory,
        auto_inject_context=False
    )

    tool_manager.register_tool(
        name="grep_files",
        description=(
            "Search for a pattern in files using grep. "
            "Returns file paths, line numbers, and matching content. "
            "Use this to find where specific text appears in your codebase."
        ),
        parameters={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regular expression pattern to search for"
                },
                "directory": {
                    "type": "string",
                    "default": ".",
                    "description": "Directory to search in (default: current directory)"
                },
                "file_pattern": {
                    "type": "string",
                    "description": "Optional file glob pattern (e.g., '*.py')"
                },
                "ignore_case": {
                    "type": "boolean",
                    "default": False,
                    "description": "Whether to ignore case in search"
                },
                "max_results": {
                    "type": "integer",
                    "default": 100,
                    "description": "Maximum number of results to return"
                }
            },
            "required": ["pattern"]
        },
        fn=grep_files,
        auto_inject_context=False
    )

    tool_manager.register_tool(
        name="delete_file",
        description=(
            "Delete a file from the filesystem. "
            "Use with caution - this cannot be undone."
        ),
        parameters={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file to delete"
                }
            },
            "required": ["file_path"]
        },
        fn=delete_file,
        auto_inject_context=False
    )

    logger.info("File tools registered: read_file, write_to_file, edit_file, list_directory, grep_files, delete_file")
