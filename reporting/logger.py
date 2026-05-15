import logging
import sys
from pathlib import Path
from typing import Optional


_SENSITIVE_KEYS = frozenset({"secret", "token", "password", "token_secret"})


class MigrateLogger:
    def __init__(self, name: str, log_file: Optional[Path] = None):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False

        self.logger.handlers.clear()

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s - %(levelname)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        self.logger.addHandler(console_handler)

        if log_file is not None:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(
                log_file, mode="a", encoding="utf-8"
            )
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )
            self.logger.addHandler(file_handler)

    def debug(self, message: str, **kwargs) -> None:
        self.logger.debug(self._build(message, kwargs))

    def info(self, message: str, **kwargs) -> None:
        self.logger.info(self._build(message, kwargs))

    def warning(self, message: str, **kwargs) -> None:
        self.logger.warning(self._build(message, kwargs))

    def error(self, message: str, **kwargs) -> None:
        self.logger.error(self._build(message, kwargs))

    def critical(self, message: str, **kwargs) -> None:
        self.logger.critical(self._build(message, kwargs))

    def exception(self, message: str, **kwargs) -> None:
        self.logger.exception(self._build(message, kwargs))

    def _build(self, message: str, extras: dict) -> str:
        if not extras:
            return message
        parts = []
        for k, v in extras.items():
            if k.lower() in _SENSITIVE_KEYS:
                parts.append(f"{k}=<redacted>")
            else:
                parts.append(f"{k}={v}")
        return f"{message} | {' | '.join(parts)}"


def setup_logger(name: str, log_dir: Path, run_id: str) -> MigrateLogger:
    log_file = log_dir / f"migrate_run_{run_id}" / "execution.log"
    return MigrateLogger(name, log_file)
