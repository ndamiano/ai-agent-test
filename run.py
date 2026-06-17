import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent / "src"))

import uvicorn

if __name__ == "__main__":
    # Configure uvicorn's logging to include our application loggers
    log_config = uvicorn.config.LOGGING_CONFIG
    log_config["formatters"]["default"]["fmt"] = "%(levelname)s:%(name)s:%(message)s"
    log_config["formatters"]["access"]["fmt"] = "%(levelname)s:%(name)s:%(message)s"

    # Add our application loggers with propagate=False to prevent duplicates
    for logger_name in ["agents", "tools", "database", "maestro", "llm_clients", "renpy"]:
        log_config["loggers"][logger_name] = {"handlers": ["default"], "level": "INFO", "propagate": False}

    uvicorn.run(
        "api.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
        log_config=log_config
    )
