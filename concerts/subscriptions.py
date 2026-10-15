"""Cached discovery feeds: calendar clients never trigger provider requests."""
import calendar as month_calendar
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .models import CalendarSubscription
from .services import calendar, search_month_events, saved_month_events, merge_discovery_events, IntegrationError


def horizon(today=None):
    today = today or timezone.localdate()
    month = today.month + 6
    year = today.year + (month-1)//12
    month = (month-1)%12+1
    end = today.replace(year=year,month=month,day=min(today.day,month_calendar.monthrange(year,month)[1]))
    return today, end


def refresh_subscription(subscription, heartbeat=lambda: None):
    subscription.attempted_at = timezone.now()
    subscription.save(update_fields=['attempted_at'])
    try:
        if not settings.TICKETMASTER_API_KEY:
            raise IntegrationError('Ticketmaster is not configured. Calendar discovery is pending.')
        start, end = horizon()
        month = start.replace(day=1)
        events = []
        limited = False
        while month <= end:
            heartbeat()
            found, truncated = search_month_events(subscription.user.profile,'',month)
            events.extend(found)
            limited |= truncated
            month = (month.replace(day=28)+timedelta(days=4)).replace(day=1)
        with transaction.atomic():
            subscription.events.set({e.pk for e in events})
            subscription.refreshed_at = timezone.now()
            subscription.error = 'Some months exceeded the provider result limit; coverage may be incomplete.' if limited else ''
            subscription.save(update_fields=['refreshed_at','error'])
    except IntegrationError as exc:
        subscription.error = str(exc)[:300]
        subscription.save(update_fields=['error'])


def subscription_events(subscription):
    start, end = horizon()
    profile = subscription.user.profile
    events = list(subscription.events.filter(local_date__gte=start,local_date__lte=end))
    month = start.replace(day=1)
    while month <= end:
        events = merge_discovery_events(events,saved_month_events(profile,'',month))
        month = (month.replace(day=28)+timedelta(days=4)).replace(day=1)
    favorites = set(subscription.user.artist_set.filter(suppressed=False).values_list('key',flat=True))
    from .services import zip_location, distance_miles
    latitude, longitude = zip_location(profile.zip_code)
    now = timezone.now()
    return [event for event in events if event.local_date and start <= event.local_date <= end
            and (not event.starts_at or event.starts_at > now)
            and event.status not in {'cancelled','canceled'}
            and event.latitude is not None and event.longitude is not None
            and distance_miles(latitude,longitude,event.latitude,event.longitude) <= profile.radius
            and any(a.get('name','').casefold() in favorites for a in event.artists)]


def render_feed(events):
    lines = ['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//Showcal//Concerts//EN',
             'CALSCALE:GREGORIAN','METHOD:PUBLISH','X-WR-CALNAME:Showcal - Favorite artists',
             'REFRESH-INTERVAL;VALUE=DURATION:PT12H','X-PUBLISHED-TTL:PT12H']
    for event in events:
        content, _ = calendar(event)
        component = content[content.index('BEGIN:VEVENT'):content.index('END:VEVENT')+len('END:VEVENT')]
        # A stable modification timestamp prevents duplicate/noisy updates on every poll.
        stamp = event.updated_at.strftime('%Y%m%dT%H%M%SZ')
        component = '\r\n'.join('DTSTAMP:'+stamp if line.startswith('DTSTAMP:') else line for line in component.split('\r\n'))
        lines.append(component)
    return '\r\n'.join([*lines,'END:VCALENDAR',''])
