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

    # ── File handler: full DEBUG log, timestamped, daily rotation ──────
    file_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler = TimedRotatingFileHandler(
        filename=log_dir / "app.log",
        when="midnight",
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.suffix = "%Y-%m-%d"
    handler.setFormatter(file_fmt)
    logger.addHandler(handler)

    # ── Console handler: INFO and above, clean single-line format ───────
    # RAG_TRACE records (level 25) use their own multi-line block format;
    # regular INFO records are printed as "[INFO] message".
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG)
    console.setFormatter(_RagConsoleFormatter())
    logger.addHandler(console)

    return logger


# ---------------------------------------------------------------------------
# Custom log level: RAG_TRACE (between DEBUG=10 and INFO=20)
# Used exclusively for the four-part structured RAG trace blocks so they
# stand out visually from regular INFO messages without cluttering the
# standard log stream.
# ---------------------------------------------------------------------------
RAG_TRACE = 15
logging.addLevelName(RAG_TRACE, "RAG")


class _RagConsoleFormatter(logging.Formatter):
    """Console formatter that renders RAG_TRACE records as decorated blocks
    and all other records as plain "[LEVEL] message" lines."""

    _DIVIDER = "=" * 72
    _SECTION  = "-" * 72

    def format(self, record: logging.LogRecord) -> str:
        if record.levelno == RAG_TRACE:
            # RAG_TRACE messages are already pre-formatted multi-line blocks;
            # just return them verbatim so the block borders print correctly.
            return record.getMessage()
        level = record.levelname
        return f"[{level}] {record.getMessage()}"


logger = setup_logger()
