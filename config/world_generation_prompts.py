CORE_SYSTEMS_PROMPT = """
Create the foundational concept for this world: {user_prompt}

Respond in JSON format:
{{
    "world_name": "Name of the world",
    "core_concept": "2-3 sentence description of what makes this world unique",
    "magic_system": "Detailed description of how magic/supernatural works, or null if none",
    "technology_level": "stone_age/bronze_age/medieval/renaissance/industrial/modern/futuristic/transcendent",
    "political_system": "tribal/feudal/empire/democracy/city_states/theocracy/imaginocracy/other",
    "reality_rules": "How does reality work differently here? What's possible that isn't in our world?"
}}

Focus on the fundamental "laws" that govern this world. What makes dreams become reality? How does technology interact with magic? What's the baseline of what's possible?
"""

RACES_PROMPT = """
World Concept: {world_concept}
Core Systems: {core_systems}

Create 4-5 distinct races that would naturally evolve in and be shaped by this world.

Respond in JSON format:
{{
    "races": [
        {{
            "name": "Race Name",
            "description": "How they're adapted to this world",
            "unique_ability": "Special trait related to world's nature",
            "culture_summary": "How they relate to the world's core concept"
        }}
    ]
}}

Consider: How would the world's unique properties shape their evolution? What biological or cultural adaptations would they develop? How do they interact with the magic/technology systems?
"""

GEOGRAPHY_PROMPT = """
World: {world_name} - {core_concept}
Magic/Reality: {magic_system}
Races: {races_summary}

Create the physical geography of this world.

Respond in JSON format:
{{
    "geography": "Overall description of climate, terrain, and physical laws",
    "unique_features": ["Feature1", "Feature2", "Feature3"],
    "climate_zones": ["Zone1", "Zone2", "Zone3"],
    "natural_phenomena": ["Phenomenon1", "Phenomenon2"]
}}

Design geography that makes sense for the races and magical systems. Where would floating islands exist? How does geography change if reality is malleable? What natural wonders exist?
"""

MAJOR_LOCATIONS_PROMPT = """
World: {world_name} - {core_concept}
Geography: {geography}
Races: {races_summary}
Systems: {core_systems}

Create 3-4 major locations that are centers of power, culture, or importance.

Respond in JSON format:
{{
    "major_locations": [
        {{
            "name": "Location Name",
            "type": "city/landmark/capital/sacred_site/fortress/other",
            "description": "What makes this place important",
            "primary_inhabitants": "Which race(s) live here primarily",
            "unique_feature": "What's special about this location"
        }}
    ]
}}

Each location should feel essential to the world. Why does it exist? What role does it serve? How does it reflect the world's unique properties?
"""

FACTIONS_PROMPT = """
World: {world_name} - {core_concept}
Races: {races_summary}
Major Locations: {locations_summary}
Systems: {core_systems}

Create 3-4 major factions with clear goals, conflicts, and relationships.

Respond in JSON format:
{{
    "factions": [
        {{
            "name": "Faction Name",
            "primary_goal": "What they want to achieve",
            "membership": "Which races/locations they're associated with",
            "methods": "How they pursue their goals",
            "conflicts": "Who they oppose and why"
        }}
    ]
}}

Design factions that arise naturally from the world's properties. In a dream-reality world, who wants to control dreams? Who wants to preserve stability? What ideological conflicts emerge?
"""

NOTABLE_CHARACTERS_PROMPT = """
World: {world_name} - {core_concept}
Races: {races_summary}
Locations: {locations_summary}
Factions: {factions_summary}

Create 3-4 notable NPCs who are important figures in this world.

Respond in JSON format:
{{
    "notable_npcs": [
        {{
            "name": "NPC Name", 
            "title_role": "Their position or title",
            "race": "Which race they belong to",
            "location": "Where they're primarily found",
            "faction": "Which faction they're associated with or 'independent'",
            "significance": "Why they're important to the world"
        }}
    ]
}}

Each NPC should have a specific role that makes sense within the established factions and locations. Avoid generic titles - make them feel integral to this specific world.
"""

HISTORICAL_EVENTS_PROMPT = """
World: {world_name} - {core_concept}
Current State:
- Races: {races_summary}
- Factions: {factions_summary}  
- NPCs: {npcs_summary}
- Locations: {locations_summary}

Create 2-4 major historical events that explain how the world reached its current state.

Respond in JSON format:
{{
    "major_world_events": [
        {{
            "name": "Event Name",
            "time_period": "When it happened (relative to present)",
            "description": "What happened and why it was significant",
            "consequences": "How it shaped the current world state"
        }}
    ]
}}

Events should explain current relationships between races, the rise of factions, the founding of major locations, or changes to the world's fundamental nature.
"""

MINOR_LOCATIONS_PROMPT = """
World: {world_name} - Complete world context
{full_world_summary}

Fill in the final details: minor locations and belief systems.

Respond in JSON format:
{{
    "minor_locations": ["Location1", "Location2", "Location3", "Location4", "Location5"],
    "religions": [
        {{
            "name": "Religion Name",
            "core_belief": "What they believe about the world's nature",
            "followers": "Which races/factions follow this"
        }}
    ]
}}

Minor locations should feel lived-in and specific to this world. Religions should arise naturally from the world's unique properties - what would people worship in a reality-bending world?
"""