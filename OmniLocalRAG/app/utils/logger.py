import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path


def setup_logger() -> logging.Logger:
    log_dir = Path(__file__).parent.parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)

    logger = logging.getLogger("omni_rag")
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    # Read retention days from config if available (avoid circular import: use
    # the raw JSON file directly so logger can be imported before config cache
    # is populated).
    backup_count = 30
    try:
        import json as _json
        _config_path = Path(__file__).parent.parent.parent / "config.json"
        if _config_path.exists():
            _cfg = _json.loads(_config_path.read_text(encoding="utf-8"))
            backup_count = int(_cfg.get("log_retention_days", 30))
    except Exception:
        pass

    handler = TimedRotatingFileHandler(
        filename=log_dir / "app.log",
        when="midnight",
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.suffix = "%Y-%m-%d"
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(handler)

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(console)

    return logger


logger = setup_logger()
