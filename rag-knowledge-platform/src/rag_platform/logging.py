"""Structured JSON logging via structlog.

Both our own loggers *and* stdlib loggers (uvicorn, sqlalchemy) are rendered as
one JSON object per line, so dev logs are machine-parseable exactly like prod
logs and there is a single pipeline to reason about.
"""

import logging
import sys

import structlog


def configure_logging(level: str) -> None:
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    # Processors shared by structlog-native events and "foreign" stdlib records,
    # so a uvicorn access log carries the same fields as our own events.
    pre_chain: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
    ]

    structlog.configure(
        processors=[
            *pre_chain,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=pre_chain,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn installs its own text-format handlers; strip them so its records
    # propagate to the root handler and come out as JSON like everything else.
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
