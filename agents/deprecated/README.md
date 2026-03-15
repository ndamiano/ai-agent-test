# Deprecated Agents

This directory contains legacy agent implementations that have been superseded by the unified `MaestroAgent`.

## Deprecated Files

- **planner.py** - Old planning agent for creating subtasks
- **orchestrator.py** - Old orchestration agent for executing subtasks

## Current Architecture

The `MaestroAgent` (`maestro_agent.py`) now owns the full lifecycle:
- Dynamic planning and evaluation
- Wave-based parallel execution
- Dependency-aware subtask management
- Self-correction and error recovery

These deprecated files are kept for reference but are no longer used in the application.

## Migration Notes

- All planning logic has been moved to `MaestroAgent._maestro_turn()`
- All execution logic uses `MaestroAgent._execute_wave()` and `MaestroAgent._execute_subtask()`
- The `TaskRunner` now interfaces directly with `MaestroAgent`

**Do not import or use these files in new code.**
