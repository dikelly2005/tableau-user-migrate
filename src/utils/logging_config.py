import logging
import sys
from typing import Optional

from config.settings import Settings


_initialized = False


def setup_logging(settings: Settings, name: str = None) -> logging.Logger:
    global _initialized
    logger = logging.getLogger(name or "tableau_user_migrate")

    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if settings.paths.log_location:
        log_file = settings.paths.log_location / "migrate.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    _initialized = True
    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def print_status(stage: str, message: str, logger: Optional[logging.Logger] = None) -> None:
    from datetime import datetime
    timestamp = datetime.now().strftime("%H:%M:%S")
    formatted = f"[{timestamp}] [{stage}] {message}"
    print(formatted)
    log = logger or logging.getLogger("tableau_user_migrate")
    log.info(f"[{stage}] {message}")
