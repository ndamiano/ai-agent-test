"""World generation tools"""

import json
import re
from typing import Optional, Dict, List, Any
from data_classes.world import WorldContext
from connectors.connector_selector import get_connector
from config.world_generation_prompts import (
    CORE_SYSTEMS_PROMPT, RACES_PROMPT, GEOGRAPHY_PROMPT, MAJOR_LOCATIONS_PROMPT,
    FACTIONS_PROMPT, NOTABLE_CHARACTERS_PROMPT, HISTORICAL_EVENTS_PROMPT, MINOR_LOCATIONS_PROMPT
)

class WorldGenerator:
    def __init__(self, context_repository):
        """
        Initialize world generator
        
        Args:
            context_repository: Repository for storing and retrieving context
        """
        self.context_repo = context_repository
        self.ai = get_connector("world_generator", "world_building", "text")
    
    def create_world(self, user_prompt: str) -> WorldContext:
        """
        Create a new world iteratively for maximum quality
        
        Args:
            user_prompt: User's description of desired world
            
        Returns:
            WorldContext: Generated world data
        """
        print("Starting iterative world generation...")
        
        # Step 1: Core systems and world concept
        core_systems = self.generate_core_systems(user_prompt)
        print(f"✓ Generated core concept: {core_systems.get('world_name', 'Unknown')}")
        
        # Step 2: Generate multiple races
        races = []
        for i in range(3):  # Generate 3 races by default
            race = self.generate_race(core_systems, races)
            if race:
                races.append(race)
        races_data = {"races": races}
        print(f"✓ Generated {len(races)} races")
        
        # Step 3: Geography
        geography_data = self.generate_geography(core_systems, races_data)
        print("✓ Generated geography and climate")
        
        # Step 4: Generate multiple major locations
        major_locations = []
        for i in range(4):  # Generate 4 major locations by default
            location = self.generate_major_location(core_systems, races_data, geography_data, major_locations)
            if location:
                major_locations.append(location)
        locations_data = {"major_locations": major_locations}
        print(f"✓ Generated {len(major_locations)} major locations")
        
        # Step 5: Generate multiple factions
        factions = []
        for i in range(3):  # Generate 3 factions by default
            faction = self.generate_faction(core_systems, races_data, locations_data, factions)
            if faction:
                factions.append(faction)
        factions_data = {"factions": factions}
        print(f"✓ Generated {len(factions)} factions")
        
        # Step 6: Generate multiple notable NPCs
        notable_npcs = []
        for i in range(5):  # Generate 5 NPCs by default
            npc = self.generate_notable_npc(core_systems, races_data, locations_data, factions_data, notable_npcs)
            if npc:
                notable_npcs.append(npc)
        npcs_data = {"notable_npcs": notable_npcs}
        print(f"✓ Generated {len(notable_npcs)} notable NPCs")
        
        # Step 7: Generate multiple historical events
        major_world_events = []
        for i in range(3):  # Generate 3 events by default
            event = self.generate_historical_event(core_systems, races_data, locations_data, factions_data, npcs_data, major_world_events)
            if event:
                major_world_events.append(event)
        events_data = {"major_world_events": major_world_events}
        print(f"✓ Generated {len(major_world_events)} historical events")
        
        # Step 8: Generate minor locations and religions
        minor_locations = []
        for i in range(5):  # Generate 5 minor locations by default
            location = self.generate_minor_location(core_systems, races_data, locations_data, factions_data, npcs_data, events_data, minor_locations)
            if location:
                minor_locations.append(location)
        
        religions = []
        for i in range(2):  # Generate 2 religions by default
            religion = self.generate_religion(core_systems, races_data, locations_data, factions_data, npcs_data, events_data, religions)
            if religion:
                religions.append(religion)
        
        minor_data = {"minor_locations": minor_locations, "religions": religions}
        print(f"✓ Generated {len(minor_locations)} minor locations and {len(religions)} religions")
        
        # Assemble final WorldContext
        world_context = self._assemble_world_context(
            core_systems, races_data, geography_data, locations_data, 
            factions_data, npcs_data, events_data, minor_data
        )
        
        # Store in repository
        self.context_repo.world_context = world_context
        print(f"✓ World '{world_context.name}' generation complete!")
        
        return world_context
    
    def generate_core_systems(self, user_prompt: str) -> Dict[str, Any]:
        """Generate core world systems and concept"""
        prompt = CORE_SYSTEMS_PROMPT.format(user_prompt=user_prompt)
        response = self.ai.generate(prompt, "")
        return self.parse_json_response(response, "core_systems")
    
    def generate_race(self, core_systems: Dict[str, Any], existing_races: List[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Generate a single race based on core systems and existing races"""
        if existing_races is None:
            existing_races = []
        
        existing_race_names = [race.get('name', '') for race in existing_races]
        existing_races_str = ', '.join(existing_race_names) if existing_race_names else 'None'
        
        prompt = f"""
        Generate a single new race for this world:
        
        World Concept: {core_systems.get('core_concept', '')}
        Core Systems: {self.format_core_systems(core_systems)}
        Existing Races: {existing_races_str}
        
        Create one unique race that fits the world and doesn't duplicate existing races.
        
        Respond in JSON format:
        {{
            "name": "Race Name",
            "description": "Brief description of the race",
            "unique_ability": "What makes them special",
            "culture_summary": "Brief cultural overview"
        }}
        """
        
        response = self.ai.generate(prompt, "")
        race_data = self.parse_json_response(response, "race")
        return race_data if race_data and race_data.get('name') else None
    
    def generate_geography(self, core_systems: Dict[str, Any], races_data: Dict[str, Any]) -> Dict[str, Any]:
        """Generate geography and climate"""
        prompt = GEOGRAPHY_PROMPT.format(
            world_name=core_systems.get('world_name', 'Unknown World'),
            core_concept=core_systems.get('core_concept', ''),
            magic_system=core_systems.get('magic_system', 'None'),
            races_summary=self.format_races_summary(races_data)
        )
        response = self.ai.generate(prompt, "")
        return self.parse_json_response(response, "geography")
    
    def generate_major_location(self, core_systems: Dict[str, Any], races_data: Dict[str, Any], 
                               geography_data: Dict[str, Any], existing_locations: List[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Generate a single major location"""
        if existing_locations is None:
            existing_locations = []
        
        existing_location_names = [loc.get('name', '') for loc in existing_locations]
        existing_locations_str = ', '.join(existing_location_names) if existing_location_names else 'None'
        
        prompt = f"""
        Generate a single major location for this world:
        
        World: {core_systems.get('world_name', 'Unknown World')}
        Concept: {core_systems.get('core_concept', '')}
        Geography: {geography_data.get('geography', '')}
        Races: {self.format_races_summary(races_data)}
        Core Systems: {self.format_core_systems(core_systems)}
        Existing Major Locations: {existing_locations_str}
        
        Create one unique major location that fits the world and doesn't duplicate existing locations.
        
        Respond in JSON format:
        {{
            "name": "Location Name",
            "type": "city/fortress/temple/etc",
            "description": "Brief description",
            "primary_inhabitants": "Main race/faction",
            "unique_feature": "What makes it special"
        }}
        """
        
        response = self.ai.generate(prompt, "")
        location_data = self.parse_json_response(response, "major_location")
        return location_data if location_data and location_data.get('name') else None
    
    def generate_faction(self, core_systems: Dict[str, Any], races_data: Dict[str, Any], 
                        locations_data: Dict[str, Any], existing_factions: List[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Generate a single faction"""
        if existing_factions is None:
            existing_factions = []
        
        existing_faction_names = [faction.get('name', '') for faction in existing_factions]
        existing_factions_str = ', '.join(existing_faction_names) if existing_faction_names else 'None'
        
        prompt = f"""
        Generate a single faction for this world:
        
        World: {core_systems.get('world_name', 'Unknown World')}
        Concept: {core_systems.get('core_concept', '')}
        Races: {self.format_races_summary(races_data)}
        Locations: {self.format_locations_summary(locations_data)}
        Core Systems: {self.format_core_systems(core_systems)}
        Existing Factions: {existing_factions_str}
        
        Create one unique faction that fits the world and doesn't duplicate existing factions.
        
        Respond in JSON format:
        {{
            "name": "Faction Name",
            "primary_goal": "What they want to achieve",
            "membership": "Who belongs to this faction",
            "methods": "How they operate",
            "conflicts": "Who they oppose"
        }}
        """
        
        response = self.ai.generate(prompt, "")
        faction_data = self.parse_json_response(response, "faction")
        return faction_data if faction_data and faction_data.get('name') else None
    
    def generate_notable_npc(self, core_systems: Dict[str, Any], races_data: Dict[str, Any], 
                            locations_data: Dict[str, Any], factions_data: Dict[str, Any], 
                            existing_npcs: List[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Generate a single notable NPC"""
        if existing_npcs is None:
            existing_npcs = []
        
        existing_npc_names = [npc.get('name', '') for npc in existing_npcs]
        existing_npcs_str = ', '.join(existing_npc_names) if existing_npc_names else 'None'
        
        prompt = f"""
        Generate a single notable NPC for this world:
        
        World: {core_systems.get('world_name', 'Unknown World')}
        Concept: {core_systems.get('core_concept', '')}
        Races: {self.format_races_summary(races_data)}
        Locations: {self.format_locations_summary(locations_data)}
        Factions: {self.format_factions_summary(factions_data)}
        Existing NPCs: {existing_npcs_str}
        
        Create one unique notable NPC that fits the world and doesn't duplicate existing NPCs.
        
        Respond in JSON format:
        {{
            "name": "NPC Name",
            "title_role": "Their title or role",
            "race": "Their race",
            "location": "Where they're found",
            "faction": "Their faction affiliation",
            "significance": "Why they're notable"
        }}
        """
        
        response = self.ai.generate(prompt, "")
        npc_data = self.parse_json_response(response, "notable_npc")
        return npc_data if npc_data and npc_data.get('name') else None
    
    def generate_historical_event(self, core_systems: Dict[str, Any], races_data: Dict[str, Any],
                                 locations_data: Dict[str, Any], factions_data: Dict[str, Any], 
                                 npcs_data: Dict[str, Any], existing_events: List[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Generate a single historical event"""
        if existing_events is None:
            existing_events = []
        
        existing_event_names = [event.get('name', '') for event in existing_events]
        existing_events_str = ', '.join(existing_event_names) if existing_event_names else 'None'
        
        prompt = f"""
        Generate a single historical event for this world:
        
        World: {core_systems.get('world_name', 'Unknown World')}
        Concept: {core_systems.get('core_concept', '')}
        Races: {self.format_races_summary(races_data)}
        Factions: {self.format_factions_summary(factions_data)}
        NPCs: {self.format_npcs_summary(npcs_data)}
        Locations: {self.format_locations_summary(locations_data)}
        Existing Events: {existing_events_str}
        
        Create one unique historical event that fits the world and doesn't duplicate existing events.
        
        Respond in JSON format:
        {{
            "name": "Event Name",
            "time_period": "When it happened",
            "description": "What happened",
            "consequences": "How it affected the world"
        }}
        """
        
        response = self.ai.generate(prompt, "")
        event_data = self.parse_json_response(response, "historical_event")
        return event_data if event_data and event_data.get('name') else None
    
    def generate_minor_location(self, core_systems: Dict[str, Any], races_data: Dict[str, Any],
                               locations_data: Dict[str, Any], factions_data: Dict[str, Any],
                               npcs_data: Dict[str, Any], events_data: Dict[str, Any], 
                               existing_minor_locations: List[str] = None) -> Optional[str]:
        """Generate a single minor location"""
        if existing_minor_locations is None:
            existing_minor_locations = []
        
        existing_locations_str = ', '.join(existing_minor_locations) if existing_minor_locations else 'None'
        full_summary = self.build_full_world_summary(
            core_systems, races_data, locations_data, factions_data, npcs_data, events_data
        )
        
        prompt = f"""
        Generate a single minor location for this world:
        
        {full_summary}
        
        Existing Minor Locations: {existing_locations_str}
        
        Create one unique minor location name that fits the world. This should be a small place like a village, outpost, ruins, grove, etc.
        
        Respond with just the location name (no JSON, just the name).
        """
        
        response = self.ai.generate(prompt, "")
        location_name = response.strip()
        return location_name if location_name and len(location_name) > 0 else None
    
    def generate_religion(self, core_systems: Dict[str, Any], races_data: Dict[str, Any],
                         locations_data: Dict[str, Any], factions_data: Dict[str, Any],
                         npcs_data: Dict[str, Any], events_data: Dict[str, Any], 
                         existing_religions: List[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Generate a single religion"""
        if existing_religions is None:
            existing_religions = []
        
        existing_religion_names = [rel.get('name', '') for rel in existing_religions]
        existing_religions_str = ', '.join(existing_religion_names) if existing_religion_names else 'None'
        full_summary = self.build_full_world_summary(
            core_systems, races_data, locations_data, factions_data, npcs_data, events_data
        )
        
        prompt = f"""
        Generate a single religion for this world:
        
        {full_summary}
        
        Existing Religions: {existing_religions_str}
        
        Create one unique religion that fits the world and doesn't duplicate existing religions.
        
        Respond in JSON format:
        {{
            "name": "Religion Name",
            "core_belief": "Main belief or principle",
            "followers": "Who typically follows this religion"
        }}
        """
        
        response = self.ai.generate(prompt, "")
        religion_data = self.parse_json_response(response, "religion")
        return religion_data if religion_data and religion_data.get('name') else None
    
    def _assemble_world_context(self, core_systems: Dict[str, Any], races_data: Dict[str, Any],
                               geography_data: Dict[str, Any], locations_data: Dict[str, Any],
                               factions_data: Dict[str, Any], npcs_data: Dict[str, Any],
                               events_data: Dict[str, Any], minor_data: Dict[str, Any]) -> WorldContext:
        """Assemble all generated data into WorldContext"""
        
        # Extract race names
        races = [race['name'] for race in races_data.get('races', [])]
        
        # Extract location names
        major_locations = [loc['name'] for loc in locations_data.get('major_locations', [])]
        minor_locations = minor_data.get('minor_locations', [])
        
        # Extract faction names
        factions = [faction['name'] for faction in factions_data.get('factions', [])]
        
        # Extract NPC names
        notable_npcs = [npc['name'] for npc in npcs_data.get('notable_npcs', [])]
        
        # Extract event names
        major_world_events = [event['name'] for event in events_data.get('major_world_events', [])]
        
        # Extract religion names
        religions = [religion['name'] for religion in minor_data.get('religions', [])]
        
        return WorldContext(
            name=core_systems.get('world_name', 'Unknown World'),
            races=races,
            major_locations=major_locations,
            minor_locations=minor_locations,
            notable_npcs=notable_npcs,
            major_world_events=major_world_events,
            factions=factions,
            default_tech_level=core_systems.get('technology_level', 'medieval'),
            magic_system=core_systems.get('magic_system'),
            political_system=core_systems.get('political_system', 'feudal'),
            geography=geography_data.get('geography'),
            religions=religions
        )
    
    # Helper methods for formatting context
    def format_core_systems(self, core_systems: Dict[str, Any]) -> str:
        """Format core systems for prompts"""
        return f"""
        Magic/Reality: {core_systems.get('magic_system', 'None')}
        Technology: {core_systems.get('technology_level', 'medieval')}
        Politics: {core_systems.get('political_system', 'feudal')}
        Reality Rules: {core_systems.get('reality_rules', 'Standard physics')}
        """
    
    def format_races_summary(self, races_data: Dict[str, Any]) -> str:
        """Format races for prompts"""
        races = races_data.get('races', [])
        if not races:
            return "No races generated yet"
        
        race_lines = []
        for race in races:
            race_lines.append(f"- {race.get('name', 'Unknown')}: {race.get('description', '')}")
        return "\n".join(race_lines)
    
    def format_locations_summary(self, locations_data: Dict[str, Any]) -> str:
        """Format locations for prompts"""
        locations = locations_data.get('major_locations', [])
        if not locations:
            return "No major locations generated yet"
        
        loc_lines = []
        for loc in locations:
            loc_lines.append(f"- {loc.get('name', 'Unknown')}: {loc.get('description', '')}")
        return "\n".join(loc_lines)
    
    def format_factions_summary(self, factions_data: Dict[str, Any]) -> str:
        """Format factions for prompts"""
        factions = factions_data.get('factions', [])
        if not factions:
            return "No factions generated yet"
        
        faction_lines = []
        for faction in factions:
            faction_lines.append(f"- {faction.get('name', 'Unknown')}: {faction.get('primary_goal', '')}")
        return "\n".join(faction_lines)
    
    def format_npcs_summary(self, npcs_data: Dict[str, Any]) -> str:
        """Format NPCs for prompts"""
        npcs = npcs_data.get('notable_npcs', [])
        if not npcs:
            return "No notable NPCs generated yet"
        
        npc_lines = []
        for npc in npcs:
            npc_lines.append(f"- {npc.get('name', 'Unknown')}: {npc.get('title_role', '')}")
        return "\n".join(npc_lines)
    
    def build_full_world_summary(self, core_systems: Dict[str, Any], races_data: Dict[str, Any],
                                 locations_data: Dict[str, Any], factions_data: Dict[str, Any],
                                 npcs_data: Dict[str, Any], events_data: Dict[str, Any]) -> str:
        """Build comprehensive world summary for final prompts"""
        return f"""
World: {core_systems.get('world_name', 'Unknown')}
Concept: {core_systems.get('core_concept', '')}

{self.format_core_systems(core_systems)}

Races:
{self.format_races_summary(races_data)}

Major Locations:
{self.format_locations_summary(locations_data)}

Factions:
{self.format_factions_summary(factions_data)}

Notable NPCs:
{self.format_npcs_summary(npcs_data)}
        """
    
    def parse_json_response(self, response: str, step_name: str) -> Dict[str, Any]:
        """Parse JSON response with fallback handling"""
        try:
            # Try to extract JSON from response
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                json_str = json_match.group()
                return json.loads(json_str)
            else:
                print(f"Warning: No JSON found in {step_name} response, using fallback")
                return self.get_fallback_data(step_name)
                
        except json.JSONDecodeError as e:
            print(f"JSON parsing failed for {step_name}: {e}")
            return self._get_fallback_data(step_name)
    
    def get_fallback_data(self, step_name: str) -> Dict[str, Any]:
        """Provide fallback data when parsing fails"""
        fallbacks = {
            "core_systems": {
                "world_name": "Generated World",
                "core_concept": "A unique fantasy realm",
                "magic_system": "Elemental magic",
                "technology_level": "medieval",
                "political_system": "feudal",
                "reality_rules": "Magic exists alongside normal physics"
            },
            "race": {
                "name": "Humans", "description": "Adaptable and numerous", 
                "unique_ability": "Versatility", "culture_summary": "Diverse cultures"
            },
            "geography": {
                "geography": "Temperate climate with varied terrain",
                "unique_features": ["Ancient forests", "Crystal caves", "Floating islands"],
                "climate_zones": ["Temperate plains", "Mountain highlands", "Coastal regions"],
                "natural_phenomena": ["Aurora magic", "Singing stones"]
            },
            "major_location": {
                "name": "Capital City", "type": "city", "description": "Center of power", 
                "primary_inhabitants": "Humans", "unique_feature": "Royal palace"
            },
            "faction": {
                "name": "Royal Guard", "primary_goal": "Protect the realm", 
                "membership": "Humans mainly", "methods": "Military force", "conflicts": "Rebels"
            },
            "notable_npc": {
                "name": "King Arthur", "title_role": "Ruler", "race": "Human", 
                "location": "Capital City", "faction": "Royal Guard", "significance": "Rules the kingdom"
            },
            "historical_event": {
                "name": "The Great Founding", "time_period": "1000 years ago", 
                "description": "Kingdom was established", "consequences": "Current political structure"
            },
            "religion": {
                "name": "The Old Faith", "core_belief": "Nature is sacred", "followers": "Elves and Druids"
            }
        }
        return fallbacks.get(step_name, {})