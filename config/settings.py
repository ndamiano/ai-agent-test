"""Configuration settings"""

import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# API Configuration
API_KEYS = {
    "openai": os.getenv("OPENAI_API_KEY", "your-openai-key-here"),
    "claude": os.getenv("CLAUDE_API_KEY", "your-claude-key-here")
}

# Model Configuration
MODEL_CONFIGS = {
    "default": {
        "temperature": float(os.getenv("DEFAULT_TEMPERATURE", "0.7")),
        "max_tokens": int(os.getenv("DEFAULT_MAX_TOKENS", "1000"))
    }
}
