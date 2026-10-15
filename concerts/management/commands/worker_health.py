from datetime import timedelta
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from concerts.models import WorkerState
class Command(BaseCommand):
    def handle(self, *args, **kwargs):
        if not WorkerState.objects.filter(name='main',heartbeat__gte=timezone.now()-timedelta(minutes=5)).exists():
            raise CommandError('No recent worker heartbeat')
