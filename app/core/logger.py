import logging

from app.core.config import settings

logger = logging.getLogger(settings.service_name)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            "[%(asctime)s] [%(levelname)s] [%(module)s] %(message)s",
            "%d/%m/%Y %I:%M:%S %p",
        )
    )
    logger.addHandler(handler)
logger.setLevel(logging.DEBUG if settings.debug else logging.INFO)
logger.propagate = False
