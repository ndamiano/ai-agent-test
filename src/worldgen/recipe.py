import hashlib
import json

SIZES = {
    "small": (96, 64),
    "medium": (128, 96),
    "large": (192, 128),
}

ARCHETYPES = {"archipelago", "continent"}

LOCATION_TYPES = {"settlement", "interior", "coastal_strip", "wilderness", "open_water", "landmark"}


def dims(recipe: dict) -> tuple:
    size = recipe.get("size")
    if size not in SIZES:
        raise ValueError(f"unknown size {size!r}, expected one of {sorted(SIZES)}")
    return SIZES[size]


def validate(recipe: dict) -> None:
    if recipe.get("archetype") not in ARCHETYPES:
        raise ValueError(f"unknown archetype {recipe.get('archetype')!r}, expected one of {sorted(ARCHETYPES)}")
    dims(recipe)
    palette = recipe.get("palette", {})
    if not palette.get("biomes"):
        raise ValueError("recipe.palette.biomes must be a non-empty list")
    for loc in recipe.get("locations", []):
        if loc.get("type") not in LOCATION_TYPES:
            raise ValueError(f"unknown location type {loc.get('type')!r}")
        if loc.get("type") == "interior" and not loc.get("host"):
            raise ValueError(f"interior location {loc.get('id')!r} needs a host")


def recipe_hash(recipe: dict) -> str:
    encoded = json.dumps(recipe, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]
