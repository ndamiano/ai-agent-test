class ContextRepository:
    """Simple context storage for generated content"""
    
    def __init__(self):
        self.story_context: Optional[StoryContext] = None
        self.world_context: Optional[WorldContext] = None
        self.locations: dict[str, LocationContext] = {}
        self.npcs: dict[str, Any] = {}
        self.factions: dict[str, Any] = {}
    
    def get_context_for_task(self, task_type: str, specific_location: str = None) -> str:
        """Get relevant context for a specific task"""
        context_parts = []
        
        # Always include story context
        if self.story_context:
            context_parts.append(f"Story Themes: {', '.join(self.story_context.themes)}")
            context_parts.append(f"Tone: {self.story_context.tone}")
        
        # Always include world basics
        if self.world_context:
            context_parts.append(self.world_context.to_context())
        
        # Add location-specific if requested
        if specific_location and specific_location in self.locations:
            loc = self.locations[specific_location]
            context_parts.append(f"\nLocation: {loc.name}")
            context_parts.append(f"Tech Level: {loc.tech_level}")
            context_parts.append(f"Culture: {loc.local_culture}")
            # etc.
        
        return "\n".join(context_parts)