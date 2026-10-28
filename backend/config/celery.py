import logging
import os

from celery import Celery
from celery.signals import worker_ready
from kombu.exceptions import OperationalError

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("crewlink")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@worker_ready.connect
def recover_sends_on_worker_start(sender=None, **kwargs):
    """Recover immediately on restart; beat also covers a lost queue/publish later."""
    from crewlink.tasks import recover_pending_announcements

    try:
        recover_pending_announcements.apply_async(retry=False)
    except OperationalError:
        logging.getLogger(__name__).warning(
            "Could not queue startup recovery; the scheduler will retry."
        )
