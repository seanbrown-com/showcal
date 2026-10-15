import re
from datetime import datetime, timedelta, timezone as dt_timezone
from urllib.parse import urlencode, urlparse
from zoneinfo import ZoneInfo
from uuid import UUID
import requests
from cryptography.fernet import Fernet
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from .models import Artist, Event, ZipLocation

class IntegrationError(Exception):
    pass

def get_json(url, **kwargs):
    try:
        response = requests.get(url, timeout=(5, 25), allow_redirects=False, **kwargs)
        if response.status_code != 200:
            raise IntegrationError(f'Provider returned HTTP {response.status_code}. Check credentials and provider access.')
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        raise IntegrationError('Provider request failed. Check connectivity and configuration.') from exc

def safe_url(value):
    p = urlparse(value or '')
    return value if p.scheme == 'https' and p.hostname and not p.username else ''

def ticketmaster(path, params):
    if not settings.TICKETMASTER_API_KEY:
        raise IntegrationError('Add TICKETMASTER_API_KEY to enable live concert and artist search.')
    return get_json('https://app.ticketmaster.com/discovery/v2/' + path, params={**params,'apikey':settings.TICKETMASTER_API_KEY})

def save_event(data):
    dates = data.get('dates', {})
    start = dates.get('start', {})
    venues = data.get('_embedded', {}).get('venues', [{}])
    venue = venues[0] if venues else {}
    images = sorted(data.get('images', []), key=lambda i: abs(i.get('width', 0)-700))
    artists = [{'id':a['id'],'name':a['name']} for a in data.get('_embedded', {}).get('attractions', []) if a.get('id') and a.get('name')]
    location = venue.get('location') or {}
    try:
        lat, lon = float(location['latitude']), float(location['longitude'])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            lat, lon = None, None
    except (KeyError, ValueError, TypeError):
        lat, lon = None, None
    event, _ = Event.objects.update_or_create(external_id=data['id'], defaults={
        'title':data['name'], 'venue':venue.get('name',''), 'city':venue.get('city',{}).get('name',''),
        'latitude':lat, 'longitude':lon,
        'starts_at':parse_datetime(start['dateTime']) if start.get('dateTime') else None,
        'local_date':None if start.get('dateTBA') or start.get('dateTBD') else parse_date(start.get('localDate') or ''), 'time_tba':bool(start.get('timeTBA') or start.get('dateTBA') or start.get('dateTBD')),
        'timezone':dates.get('timezone') or venue.get('timezone') or 'UTC', 'url':safe_url(data.get('url')),
        'image':safe_url(images[0].get('url')) if images else '', 'artists':artists,
        'status':dates.get('status',{}).get('code','unknown')})
    return event

def geohash(latitude, longitude, precision=8):
    alphabet = '0123456789bcdefghjkmnpqrstuvwxyz'
    ranges = [[-180.0, 180.0], [-90.0, 90.0]]
    point = [longitude, latitude]
    result, value = '', 0
    for bit in range(precision * 5):
        axis = bit % 2
        middle = sum(ranges[axis]) / 2
        high = point[axis] >= middle
        value = (value << 1) | int(high)
        ranges[axis][0 if high else 1] = middle
        if bit % 5 == 4:
            result += alphabet[value]
            value = 0
    return result

def zip_location(zip_code):
    cached = ZipLocation.objects.filter(pk=zip_code).first()
    if cached:
        return cached.latitude, cached.longitude
    try:
        data = get_json('https://api.zippopotam.us/us/' + zip_code)
        place = data['places'][0]
        latitude, longitude = float(place['latitude']), float(place['longitude'])
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise ValueError('Coordinates out of range')
    except (IntegrationError, KeyError, IndexError, ValueError, TypeError) as exc:
        raise IntegrationError('Could not locate that US ZIP code. Check your profile ZIP or try again later.') from exc
    ZipLocation.objects.update_or_create(zip_code=zip_code, defaults={'latitude':latitude,'longitude':longitude})
    return latitude, longitude

def distance_miles(lat1, lon1, lat2, lon2):
    from math import radians, sin, cos, asin, sqrt
    lat1, lon1, lat2, lon2 = map(radians, (lat1, lon1, lat2, lon2))
    a = sin((lat2-lat1)/2)**2 + cos(lat1)*cos(lat2)*sin((lon2-lon1)/2)**2
    return 3958.7613 * 2 * asin(sqrt(min(1, max(0, a))))

def search_events(profile, query='', page=0, *, month_start=None):
    latitude, longitude = zip_location(profile.zip_code)
    params = {'geoPoint':geohash(latitude,longitude),'radius':profile.radius,'unit':'miles','countryCode':'US','classificationName':'music','keyword':query,'startDateTime':timezone.now().strftime('%Y-%m-%dT%H:%M:%SZ'),'sort':'date,asc','size':200 if month_start else 24,'page':page}
    if month_start:
        month_end = (month_start.replace(day=28)+timedelta(days=4)).replace(day=1)-timedelta(days=1)
        params['localStartDateTime'] = f'{month_start.isoformat()}T00:00:00,{month_end.isoformat()}T23:59:59'
        params.update(includeTBA='no',includeTBD='no')
    data = ticketmaster('events.json', params)
    events = []
    for raw in data.get('_embedded',{}).get('events',[]):
        try:
            location = raw['_embedded']['venues'][0]['location']
            lat, lon = float(location['latitude']), float(location['longitude'])
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                continue
            distance = distance_miles(latitude,longitude,lat,lon)
        except (KeyError, IndexError, TypeError, ValueError):
            continue  # Unknown venue coordinates cannot establish the requested radius.
        if distance <= profile.radius:
            if month_start:
                local_date = raw.get('dates',{}).get('start',{}).get('localDate') or ''
                if not month_start.isoformat() <= local_date <= month_end.isoformat():
                    continue
            event = save_event(raw)
            event.distance = round(distance,1)
            events.append(event)
    return events, min(data.get('page',{}).get('totalPages',0), 41)

def search_month_events(profile, query, month_start):
    events, pages = search_events(profile,query,month_start=month_start)
    for page in range(1,min(pages,5)):
        more, _ = search_events(profile,query,page,month_start=month_start)
        events.extend(more)
    # Ticketmaster limits deep paging to the first 1,000 results.
    unique = {event.external_id:event for event in events}
    return sorted(unique.values(),key=lambda event:(str(event.local_date),str(event.starts_at or ''),event.title)), pages > 5

def saved_month_events(profile, query, month_start):
    """Include separately imported AXS listings, with the same geographic limits."""
    month_end = (month_start.replace(day=28)+timedelta(days=4)).replace(day=1)
    candidates = Event.objects.filter(source='axs',local_date__gte=month_start,local_date__lt=month_end,
                                      latitude__isnull=False,longitude__isnull=False).exclude(status__in=['cancelled','canceled'])
    if not candidates.exists():
        return []
    latitude, longitude = zip_location(profile.zip_code)
    now = timezone.now()
    result = []
    for event in candidates:
        if event.starts_at and event.starts_at <= now:
            continue
        if not event.starts_at and event.local_date < timezone.localdate():
            continue
        if not (-90 <= event.latitude <= 90 and -180 <= event.longitude <= 180):
            continue
        text = ' '.join([event.title,event.venue,event.city,*[a.get('name','') for a in event.artists]]).casefold()
        if not all(word in text for word in query.casefold().split()):
            continue
        distance = distance_miles(latitude,longitude,event.latitude,event.longitude)
        if distance <= profile.radius:
            event.distance = round(distance,1)
            result.append(event)
    return result

def merge_discovery_events(events, saved):
    # The same AXS link can also appear in Ticketmaster Discovery.
    unique = {}
    for event in [*events,*saved]:
        url = urlparse(event.url)
        key = (url.hostname, url.path.rstrip('/')) if event.url else event.external_id
        unique.setdefault(key,event)
    return sorted(unique.values(),key=lambda event:(str(event.local_date),str(event.starts_at or ''),event.title))

def search_artists(query):
    return ticketmaster('attractions.json', {'keyword':query,'classificationName':'music','size':20}).get('_embedded',{}).get('attractions',[])

def encrypt(token):
    if not settings.CREDENTIAL_KEY:
        raise IntegrationError('Set CREDENTIAL_KEY before saving Jellyfin credentials.')
    try:
        return Fernet(settings.CREDENTIAL_KEY.encode()).encrypt(token.encode()).decode()
    except ValueError as exc:
        raise IntegrationError('CREDENTIAL_KEY must be a valid Fernet key.') from exc

def decrypt_jellyfin_token(encrypted):
    if not settings.CREDENTIAL_KEY or not encrypted:
        raise IntegrationError('Configure the Jellyfin connection first.')
    try:
        return Fernet(settings.CREDENTIAL_KEY.encode()).decrypt(encrypted.encode()).decode()
    except Exception as exc:
        raise IntegrationError('Cannot decrypt Jellyfin token; check CREDENTIAL_KEY.') from exc

def resolve_jellyfin_user(url, identifier, token):
    """Verify a user ID or resolve a username without storing the server user list."""
    identifier = identifier.strip()
    try:
        expected_id = UUID(identifier).hex
    except ValueError:
        expected_id = None
    endpoint = '/Users/'+expected_id if expected_id else '/Users'
    try:
        data = get_json(url.rstrip('/')+endpoint,headers={'X-Emby-Token':token})
    except IntegrationError as exc:
        raise IntegrationError('Could not verify the Jellyfin user. Check the server URL and token permissions. If username lookup is restricted, enter the internal user ID instead.') from exc
    if expected_id:
        users = [data] if isinstance(data,dict) else []
    else:
        if not isinstance(data,list):
            raise IntegrationError('Jellyfin returned an unexpected user-list response. Check the server URL.')
        users = [user for user in data if isinstance(user,dict) and isinstance(user.get('Name'),str) and user['Name'].casefold()==identifier.casefold()]
    if len(users) != 1:
        raise IntegrationError('Could not uniquely find that Jellyfin username. Check the spelling or enter the internal user ID; the token must be allowed to see that user.')
    try:
        actual_id = UUID(users[0]['Id']).hex
    except (KeyError,ValueError,TypeError,AttributeError) as exc:
        raise IntegrationError('Jellyfin returned an invalid user ID. Connection was not saved.') from exc
    if expected_id and actual_id != expected_id:
        raise IntegrationError('Jellyfin returned a different user. Connection was not saved.')
    return actual_id

def sync_jellyfin(profile):
    token = decrypt_jellyfin_token(profile.jellyfin_token)
    names = {}
    # Collect all pages before writing so a failed sync cannot partially import a library.
    for endpoint in ['Artists','Artists/AlbumArtists']:
        offset = 0
        while True:
            data = get_json(profile.jellyfin_url.rstrip('/') + '/' + endpoint, headers={'X-Emby-Token':token}, params={'UserId':profile.jellyfin_user_id,'StartIndex':offset,'Limit':200,'Recursive':'true'})
            items = data.get('Items', [])
            for item in items:
                if item.get('Name'):
                    name = item['Name'].strip()[:250]
                    names[name.casefold()] = name
            offset += len(items)
            if not items or offset >= data.get('TotalRecordCount', offset):
                break
            if offset > 100000:
                raise IntegrationError('Library exceeds the supported artist limit.')
    from django.db import transaction
    added = 0
    with transaction.atomic():
        for key, name in names.items():
            _, created = Artist.objects.get_or_create(user=profile.user, key=key, defaults={'name':name,'source':'jellyfin'})
            added += int(created)
        profile.last_sync = timezone.now()
        profile.sync_attempt = profile.last_sync
        profile.sync_error = ''
        profile.save(update_fields=['last_sync','sync_attempt','sync_error'])
    return added

def facebook_candidates(event):
    query = f'site:facebook.com/events "{event.title}" "{event.venue}" {event.local_date or ""}'
    if not settings.BRAVE_API_KEY:
        return []
    data = get_json('https://api.search.brave.com/res/v1/web/search', headers={'X-Subscription-Token':settings.BRAVE_API_KEY}, params={'q':query,'count':5})
    return [r for r in data.get('web',{}).get('results',[]) if urlparse(r.get('url','')).hostname in {'facebook.com','www.facebook.com','m.facebook.com'} and re.match(r'^/events/\d+', urlparse(r.get('url','')).path)]

def calendar(event):
    def escape(value):
        return str(value).replace('\\','\\\\').replace('\r','').replace('\n','\\n').replace(';','\\;').replace(',','\\,')
    if not event.local_date:
        raise IntegrationError('The concert date has not been announced yet.')
    datefmt = '%Y%m%dT%H%M%SZ'
    if event.starts_at and not event.time_tba:
        start = event.starts_at.astimezone(dt_timezone.utc)
        timing = [f'DTSTART:{start.strftime(datefmt)}',f'DTEND:{(start+timedelta(hours=3)).strftime(datefmt)}']
        google_dates = start.strftime(datefmt)+'/'+(start+timedelta(hours=3)).strftime(datefmt)
    else:
        timing = [f'DTSTART;VALUE=DATE:{event.local_date:%Y%m%d}',f'DTEND;VALUE=DATE:{event.local_date+timedelta(days=1):%Y%m%d}']
        google_dates = f'{event.local_date:%Y%m%d}/{event.local_date+timedelta(days=1):%Y%m%d}'
    description = 'Concert details: '+event.url+'\nEnd time is an estimate. Check the venue for final times.'
    lines = ['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//Showcal//Concerts//EN','CALSCALE:GREGORIAN','METHOD:PUBLISH','BEGIN:VEVENT',f'UID:{event.external_id}@ticket-watch',f'DTSTAMP:{timezone.now():%Y%m%dT%H%M%SZ}',*timing,'SUMMARY:'+escape(event.title),'LOCATION:'+escape(event.venue+', '+event.city),'DESCRIPTION:'+escape(description),'URL:'+escape(event.url),'END:VEVENT','END:VCALENDAR']
    # RFC 5545: fold at 75 octets without splitting a UTF-8 character.
    folded = []
    for line in lines:
        chunk = ''
        for char in line:
            if len((chunk+char).encode()) > 75:
                folded.append(chunk)
                chunk = ' '
            chunk += char
        folded.append(chunk)
    google = 'https://calendar.google.com/calendar/render?'+urlencode({'action':'TEMPLATE','text':event.title,'dates':google_dates,'location':event.venue+', '+event.city,'details':description})
    return '\r\n'.join(folded)+'\r\n', google
