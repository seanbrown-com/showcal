import time
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone
from concerts.models import Profile, WorkerState
from concerts.services import sync_jellyfin

def tick():
    now = timezone.now()
    state, _ = WorkerState.objects.get_or_create(name='main', defaults={'heartbeat':now})
    # Atomic lease prevents two workers from refreshing the same integrations concurrently.
    acquired = WorkerState.objects.filter(pk=state.pk).filter(Q(lease_until__isnull=True)|Q(lease_until__lt=now)).update(lease_until=now+timedelta(minutes=10),heartbeat=now)
    if not acquired:
        return
    def heartbeat():
        WorkerState.objects.filter(pk=state.pk).update(heartbeat=timezone.now(),lease_until=timezone.now()+timedelta(minutes=10))
    try:
        profiles = Profile.objects.exclude(jellyfin_token='').filter(Q(last_sync__isnull=True)|Q(last_sync__lt=now-timedelta(days=7))).filter(Q(sync_attempt__isnull=True)|Q(sync_attempt__lt=now-timedelta(hours=1)))
        for profile in profiles:
            heartbeat()
            profile.sync_attempt = timezone.now()
            profile.save(update_fields=['sync_attempt'])
            try:
                sync_jellyfin(profile)
            except Exception:
                profile.sync_error = 'Jellyfin sync failed. Check connection and credentials; worker will retry.'
                profile.save(update_fields=['sync_error'])
        from concerts.models import CalendarSubscription
        from concerts.subscriptions import refresh_subscription
        subscriptions = CalendarSubscription.objects.filter(user__is_active=True).filter(
            Q(attempted_at__isnull=True)|Q(attempted_at__lt=now-timedelta(days=1)))
        for subscription in subscriptions.select_related('user__profile'):
            heartbeat()
            try:
                refresh_subscription(subscription,heartbeat)
            except Exception:
                subscription.error = 'Calendar discovery failed; the worker will retry. Previously fetched shows are retained.'
                subscription.save(update_fields=['error'])
    finally:
        WorkerState.objects.filter(pk=state.pk).update(heartbeat=timezone.now(),lease_until=None)

class Command(BaseCommand):
    help = 'Refresh concert calendars and sync Jellyfin artists (run one worker).'
    def add_arguments(self, parser):
        parser.add_argument('--once',action='store_true')
    def handle(self, *args, **options):
        while True:
            tick()
            if options['once']:
                return
            time.sleep(60)
