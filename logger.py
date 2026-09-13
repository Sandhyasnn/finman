"""
Application-wide logging for FinMan.

The logger is configured once during application startup using the
logging settings from the validated FinMan configuration.

Other modules should import `logger` and use it directly. They should
not configure their own handlers or logging levels.
"""
import logging

logger = logging.getLogger("finman")

def configure_logger(level: str, log_file: str | None = None,) -> None:
    """
    Configure the FinMan application logger.
    Args:
        level: Logging level from the FinMan configuration.
        log_file: Optional file path for application logs.
    Configuration is performed once during application startup.
    """
    logger.setLevel(getattr(logging, level.upper()))

    # Avoid adding duplicate handlers if startup/configuration is
    # called more than once during development or testing.
    if logger.handlers:
        return

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(funcName)s | %(message)s")

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8",)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
