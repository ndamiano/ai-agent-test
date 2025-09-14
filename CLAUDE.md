# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AI Creative Agent System for generating Skyrim mods, visual novels, and other creative content using AI agents. The system uses an iterative, multi-step approach to generate rich fantasy worlds with races, locations, characters, and lore.

## Commands

### Running the Application
```bash
python main.py
```

### Testing
```bash
python -m unittest discover tests/
```

The test suite uses Python's built-in unittest framework.

## Architecture

This is a modular Python application structured around creative content generation:

### Core Components

- **Project (`project.py`)**: Top-level container for creative projects. Each Project has its own context repository and generators
- **Data Classes (`data_classes/`)**: Define the core data structures:
  - `WorldContext`: Complete world definition with races, locations, NPCs, factions, events
  - `Character`: Character definitions
  - `Location`: Location data
  - `Story`: Story/narrative structures
- **Generators (`generators/`)**: AI-powered content generation:
  - `WorldGenerator`: Multi-step iterative world creation using AI connectors
- **Repository (`repository/`)**: Context storage and retrieval
- **Tools (`tools/`)**: Utility functions for content generation
- **Connectors (`connectors/`)**: AI service integrations for content generation
- **Agents (`agents/`)**: Conversational AI agents (currently stubs)

### Key Patterns

1. **Iterative Generation**: `WorldGenerator` creates worlds in 8 sequential steps, building context progressively:
   - Core systems → Races → Geography → Major locations → Factions → NPCs → Events → Minor details

2. **Context Accumulation**: Each generation step uses results from previous steps to maintain consistency

3. **Fallback Handling**: Robust JSON parsing with fallback data when AI responses fail

4. **Modular Design**: Clear separation between data structures, generation logic, and AI connectors

### Data Flow

```
Project → WorldGenerator → AI Connectors → WorldContext → ContextRepository
```

The `WorldContext.to_context()` method formats world data for different AI consumption scenarios (character creation, world building, etc.).

## File Structure

- `main.py`: Entry point demonstrating world creation
- `project.py`: Main project management class
- `data_classes/`: Core data models using dataclasses
- `generators/`: AI-powered content generators with iterative approaches  
- `connectors/`: AI service integrations
- `repository/`: Context storage layer
- `tools/`: Utility functions
- `agents/`: Conversational AI agents (minimal implementation)
- `tests/`: Unit tests using unittest framework
- `config/`: Configuration and prompt templates
- `output/`: Generated content storage

## Development Notes

- Uses dataclasses for structured data
- Empty `requirements.txt` indicates minimal external dependencies
- AI connector system allows pluggable LLM backends
- Extensive error handling and fallback mechanisms in world generation