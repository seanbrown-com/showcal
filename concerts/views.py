from datetime import date, timedelta
from urllib.parse import urlencode, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST, require_GET
from .forms import SignupForm, ProfileForm, JellyfinForm
from .models import Profile, Artist, Event, WorkerState
from .services import IntegrationError, search_month_events, search_artists, ticketmaster, calendar, encrypt, sync_jellyfin, facebook_candidates, decrypt_jellyfin_token, resolve_jellyfin_user
from .services import saved_month_events, merge_discovery_events

def signup(request):
    if request.user.is_authenticated:
        return redirect('home')
    form = SignupForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            user = form.save()
            Profile.objects.create(user=user,zip_code=form.cleaned_data['zip_code'],radius=form.cleaned_data['radius'])
        login(request,user)
        return redirect('home')
    return render(request,'registration/signup.html',{'form':form})

def decorate(event):
    try:
        event.display_time = event.starts_at.astimezone(ZoneInfo(event.timezone)) if event.starts_at else None
    except ZoneInfoNotFoundError:
        event.display_time = event.starts_at
    return event

@login_required
def home(request):
    profile = request.user.profile
    query = request.GET.get('q','').strip()[:200]
    favorites_only = request.GET.get('favorites','1') != '0'
    events, error, results_limited = [], '', False
    current_month = timezone.localdate().replace(day=1)
    try:
        month_start = date.fromisoformat(request.GET.get('month',current_month.strftime('%Y-%m'))+'-01')
        offset = (month_start.year-current_month.year)*12+month_start.month-current_month.month
        if not 0 <= offset <= 120:
            month_start = current_month
    except ValueError:
        month_start = current_month
    next_month = (month_start.replace(day=28)+timedelta(days=4)).replace(day=1)
    month_end = next_month-timedelta(days=1)
    previous_month = (month_start-timedelta(days=1)).replace(day=1) if month_start > current_month else None
    if settings.TICKETMASTER_API_KEY:
        try:
            events, results_limited = search_month_events(profile,query,month_start)
        except IntegrationError as exc:
            error = str(exc)
    else:
        error = 'Connect Ticketmaster to discover real concerts near you. Add your API key in the server configuration.'
    try:
        events = merge_discovery_events(events,saved_month_events(profile,query,month_start))
    except IntegrationError as exc:
        error = str(exc)
    favorites = set(request.user.artist_set.filter(suppressed=False).values_list('key',flat=True))
    for event in events:
        decorate(event)
        event.favorite_match = any(a['name'].casefold() in favorites for a in event.artists)
    if favorites_only:
        events = [e for e in events if e.favorite_match]
    return render(request,'home.html',{'events':events,'query':query,'profile':profile,'error':error,'artist_count':len(favorites),'month_start':month_start,'month_end':month_end,'next_month':next_month,'previous_month':previous_month,'results_limited':results_limited,'favorites_only':favorites_only})

@login_required
@require_GET
def detail(request, pk):
    event = decorate(get_object_or_404(Event,pk=pk))
    try:
        _, google_url = calendar(event)
    except IntegrationError:
        google_url = ''
    fb_query = f'{event.title} {event.venue} {event.local_date or ""}'
    axs_search = 'https://www.google.com/search?' + urlencode({'q':'site:axs.com/events '+fb_query})
    return render(request,'detail.html',{'event':event,'google_url':google_url,'axs_search':axs_search,'facebook_search':'https://www.facebook.com/search/events/?'+urlencode({'q':fb_query}),'web_search':'https://www.google.com/search?'+urlencode({'q':'site:facebook.com/events '+fb_query}),'brave_ready':bool(settings.BRAVE_API_KEY)})

@login_required
def calendar_download(request,pk):
    event = get_object_or_404(Event,pk=pk)
    try:
        ics, _ = calendar(event)
    except IntegrationError as exc:
        return HttpResponse(str(exc),status=400)
    response = HttpResponse(ics,content_type='text/calendar; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="concert.ics"'
    return response

@login_required
def artists(request):
    results, error = [], ''
    query = request.GET.get('q','').strip()[:200]
    if query:
        try:
            results = search_artists(query)
        except IntegrationError as exc:
            error = str(exc)
    return render(request,'artists.html',{'artists':Artist.objects.filter(user=request.user,suppressed=False),'results':results,'query':query,'error':error})

@login_required
@require_POST
def add_artist(request):
    external_id = request.POST.get('external_id','')[:150]
    import re
    if not re.fullmatch(r'[A-Za-z0-9_-]+', external_id):
        return HttpResponse('Invalid artist ID',status=400)
    try:
        data = ticketmaster(f'attractions/{external_id}.json',{})
        name = data['name'][:250]
        Artist.objects.update_or_create(user=request.user,key=name.casefold(),defaults={'name':name,'external_id':external_id,'source':'manual','suppressed':False})
        messages.success(request,f'{name} added to your favorites.')
    except (IntegrationError,KeyError) as exc:
        messages.error(request,str(exc))
    return redirect('artists')

@login_required
@require_POST
def remove_artist(request,pk):
    artist = get_object_or_404(Artist,pk=pk,user=request.user)
    artist.suppressed = True
    artist.save(update_fields=['suppressed'])
    messages.success(request,'Artist removed. Jellyfin sync will respect this preference.')
    return redirect('artists')

@login_required
def profile(request):
    p = request.user.profile
    form = ProfileForm(request.POST if request.method=='POST' and request.POST.get('action')=='profile' else None,instance=p)
    jf = JellyfinForm(request.POST if request.method=='POST' and request.POST.get('action')=='jellyfin' else None,initial={'url':p.jellyfin_url,'user_id':p.jellyfin_user_id})
    if request.method=='POST':
        action = request.POST.get('action')
        if action=='profile' and form.is_valid():
            form.save()
            messages.success(request,'Preferences saved.')
            return redirect('profile')
        if action=='jellyfin' and jf.is_valid():
            try:
                url = jf.cleaned_data['url'].rstrip('/')
                parsed = urlparse(url)
                if parsed.scheme not in {'http','https'} or parsed.username or parsed.query or parsed.fragment or parsed.hostname in {'169.254.169.254','metadata.google.internal'}:
                    raise IntegrationError('Use an HTTP(S) server URL without credentials or a query string.')
                if p.jellyfin_token and url != p.jellyfin_url and not jf.cleaned_data['token']:
                    raise IntegrationError('Enter a token again when changing the server URL.')
                if not p.jellyfin_token and not jf.cleaned_data['token']:
                    raise IntegrationError('Enter a Jellyfin API key or access token.')
                encrypted_token = encrypt(jf.cleaned_data['token']) if jf.cleaned_data['token'] else p.jellyfin_token
                token = jf.cleaned_data['token'] or decrypt_jellyfin_token(encrypted_token)
                user_id = resolve_jellyfin_user(url,jf.cleaned_data['user_id'],token)
                p.jellyfin_token = encrypted_token
                p.jellyfin_url = url
                p.jellyfin_user_id = user_id
                p.last_sync = None
                p.sync_attempt = None
                p.sync_error = ''
                p.save()
                messages.success(request,'Jellyfin user verified and connection saved. The worker will import your artists shortly.')
                return redirect('profile')
            except IntegrationError as exc:
                jf.add_error(None,str(exc))
    state = WorkerState.objects.filter(name='main').first()
    worker_ok = bool(state and state.heartbeat > timezone.now()-timedelta(minutes=5))
    return render(request,'profile.html',{'form':form,'jf':jf,'profile':p,'worker_ok':worker_ok,'worker':state,'ticketmaster_ready':bool(settings.TICKETMASTER_API_KEY),'brave_ready':bool(settings.BRAVE_API_KEY)})

@login_required
@require_POST
def jellyfin_sync(request):
    p = request.user.profile
    p.last_sync = None
    p.sync_attempt = None
    p.save(update_fields=['last_sync','sync_attempt'])
    messages.success(request,'Artist import queued for the background worker.')
    return redirect('profile')

@login_required
@require_POST
def jellyfin_disconnect(request):
    p = request.user.profile
    p.jellyfin_url = p.jellyfin_token = p.jellyfin_user_id = p.sync_error = ''
    p.last_sync = p.sync_attempt = None
    p.save()
    messages.success(request,'Jellyfin disconnected. Your existing favorites are kept.')
    return redirect('profile')

@login_required
def facebook(request,pk):
    event = get_object_or_404(Event,pk=pk)
    try:
        results = facebook_candidates(event)
        error = ''
    except IntegrationError as exc:
        results, error = [], str(exc)
    return render(request,'facebook.html',{'event':event,'results':results,'error':error})

def health(request):
    return JsonResponse({'status':'ok'})

@login_required
def calendar_settings(request):
    import uuid
    from .models import CalendarSubscription
    from .subscriptions import subscription_events, horizon
    from django.urls import reverse
    subscription = CalendarSubscription.objects.filter(user=request.user).first()
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'disable':
            if subscription:
                subscription.delete()
            messages.success(request,'Calendar subscription disabled. Its old link no longer works.')
        elif action in {'enable','rotate','refresh'}:
            subscription, _ = CalendarSubscription.objects.get_or_create(user=request.user)
            if action == 'rotate':
                subscription.token = uuid.uuid4()
            subscription.attempted_at = None
            subscription.save(update_fields=['token','attempted_at'])
            messages.success(request,'Calendar refresh queued for the background worker.')
        return redirect('calendar_settings')
    events, error, feed_url = [], '', ''
    if subscription:
        feed_url = settings.APP_URL + reverse('calendar_feed',args=[subscription.token])
        try:
            events = [decorate(event) for event in subscription_events(subscription)]
        except IntegrationError as exc:
            error = str(exc)
    return render(request,'calendar_subscription.html',{'subscription':subscription,'events':events,'error':error,
        'feed_url':feed_url,'webcal_url':feed_url.replace('https://','webcal://',1).replace('http://','webcal://',1),
        'horizon_end':horizon()[1]})


def calendar_feed(request, token):
    from .models import CalendarSubscription
    from .subscriptions import subscription_events, render_feed
    subscription = get_object_or_404(CalendarSubscription.objects.select_related('user__profile'),token=token,user__is_active=True)
    if not subscription.refreshed_at:
        response = HttpResponse('Calendar discovery is pending. Please try again later.',status=503)
        response['Retry-After'] = '3600'
    else:
        try:
            response = HttpResponse(render_feed(subscription_events(subscription)),content_type='text/calendar; charset=utf-8')
            response['Content-Disposition'] = 'inline; filename="showcal.ics"'
        except IntegrationError:
            response = HttpResponse('Calendar is temporarily unavailable.',status=503)
            response['Retry-After'] = '3600'
    response['Cache-Control'] = 'private, no-store'
    response['Referrer-Policy'] = 'no-referrer'
    return response
