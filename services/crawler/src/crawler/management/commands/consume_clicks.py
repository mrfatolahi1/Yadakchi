import logging
from typing import Any

from django.core.management.base import BaseCommand

from crawler.consumers.clicks import ClickConsumerRunner, build_click_consumer

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Consume click events into the local crawl-tiering read model"

    def handle(self, *args: Any, **options: Any) -> None:
        del args, options
        consumer = build_click_consumer()
        runner = ClickConsumerRunner(consumer)
        try:
            while True:
                runner.run_once()
        except KeyboardInterrupt:
            logger.info("click_consumer_stopped")
        finally:
            consumer.close()
