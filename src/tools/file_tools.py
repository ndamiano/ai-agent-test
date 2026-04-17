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

        # Detect binary files by sniffing the first 8KB for null bytes
        with open(path, 'rb') as fb:
            chunk = fb.read(8192)
        if b'\x00' in chunk:
            import mimetypes
            mime, _ = mimetypes.guess_type(str(path))
            return {
                "success": False,
                "error": (
                    f"File is binary ({mime or 'unknown type'}, {path.stat().st_size} bytes) "
                    f"and cannot be read as text. Use a dedicated tool or download it directly."
                ),
                "file_path": str(path),
                "is_binary": True,
                "size_bytes": path.stat().st_size,
                "mime_type": mime,
            }

        with open(path, 'r', encoding='utf-8', errors='replace') as f:
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
    try:
        from tools.execution_context import resolve_base_path
        path = resolve_base_path(file_path)

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
    try:
        from tools.execution_context import resolve_base_path
        path = resolve_base_path(file_path)

        if not path.exists():
            return {
                "success": False,
                "error": f"File not found: {file_path}",
                "file_path": str(path)
            }

        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        if old_text not in content:
            return {
                "success": False,
                "error": f"Text to replace not found in file",
                "file_path": str(path),
                "old_text": old_text[:100] + "..." if len(old_text) > 100 else old_text
            }

        occurrences = content.count(old_text)
        if occurrences > 1:
            return {
                "success": False,
                "error": f"Text appears {occurrences} times in file. old_text must be unique for safety.",
                "file_path": str(path),
                "occurrences": occurrences
            }

        new_content = content.replace(old_text, new_text)

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


def list_files(
    directory: str = ".",
    pattern: Optional[str] = None,
    recursive: bool = False,
    max_depth: int = 3,
) -> Dict[str, Any]:
    try:
        from tools.execution_context import resolve_base_path
        path = resolve_base_path(directory)

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
            matches = path.glob(pattern)
            for item in matches:
                rel_path = str(item.relative_to(path))
                depth = len(Path(rel_path).parts)
                if depth <= max_depth:
                    if item.is_file():
                        files.append(rel_path)
                    elif item.is_dir():
                        dirs.append(rel_path)
        elif pattern:
            matches = path.glob(pattern)
            for item in matches:
                rel_path = str(item.relative_to(path))
                if item.is_file():
                    files.append(rel_path)
                elif item.is_dir():
                    dirs.append(rel_path)
        elif recursive:
            for item in path.rglob('*'):
                rel_path = str(item.relative_to(path))
                depth = len(Path(rel_path).parts)
                if depth <= max_depth:
                    if item.is_file():
                        files.append(rel_path)
                    elif item.is_dir():
                        dirs.append(rel_path)
        else:
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


def register_file_tools():
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
        name="list_files",
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
        fn=list_files,
        auto_inject_context=False
    )

    logger.info("File tools registered: read_file, write_to_file, edit_file, list_files")
