from datetime import timedelta
from unittest.mock import patch
from cryptography.fernet import Fernet
from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings, Client
from django.utils import timezone
from .models import Profile, Artist, Event, WorkerState, ZipLocation
from .services import calendar, IntegrationError, sync_jellyfin, encrypt, search_events, save_event
from .management.commands.worker import tick

class AppTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('listener',email='listener@example.com',password='A-test-password-748!')
        self.profile = Profile.objects.create(user=self.user,zip_code='90210')
        ZipLocation.objects.create(zip_code='90210',latitude=34.09,longitude=-118.40)
        self.other = User.objects.create_user('other',email='other@example.com',password='A-test-password-912!')
        Profile.objects.create(user=self.other,zip_code='10001')
        self.event = Event.objects.create(external_id='event-1',title='Björk, Live; Tonight',venue='Test Hall',city='Los Angeles',starts_at=timezone.now()+timedelta(days=20),local_date=(timezone.now()+timedelta(days=20)).date(),url='https://www.ticketmaster.com/event/test')
        self.client.force_login(self.user)
    def test_pages_render(self):
        for url in ['/','/artists/','/calendar/','/settings/',f'/events/{self.event.pk}/',f'/events/{self.event.pk}/facebook/']:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code,200)
    def test_auth_required(self):
        self.client.logout()
        self.assertEqual(self.client.get('/').status_code,302)
        self.assertEqual(self.client.get('/login/').status_code,200)
        self.assertEqual(self.client.get('/signup/').status_code,200)
    def test_signup_and_profile(self):
        self.client.logout()
        response = self.client.post('/signup/',{'username':'newlistener','email':'new@example.com','password1':'Unique-phrase-7489!','password2':'Unique-phrase-7489!','zip_code':'94103','radius':50})
        self.assertEqual(response.status_code,302)
        self.assertEqual(User.objects.get(username='newlistener').profile.zip_code,'94103')
    def test_user_cannot_remove_others_artist(self):
        a = Artist.objects.create(user=self.user,name='Björk',key='björk')
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(f'/artists/{a.pk}/remove/').status_code,404)
    def test_mutations_require_post_and_csrf(self):
        self.assertEqual(self.client.get('/settings/jellyfin/sync/').status_code,405)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post('/settings/jellyfin/sync/').status_code,403)
    def test_calendar_escaping_and_folding(self):
        self.event.title += ' é'*100+'\nBEGIN:VEVENT'
        ics, google = calendar(self.event)
        self.assertIn('Björk\\, Live\\; Tonight',ics)
        self.assertIn('\\nBEGIN:VEVENT',ics)
        self.assertTrue(all(len(line.encode())<=75 for line in ics.split('\r\n')))
        self.assertIn('calendar.google.com',google)
        self.assertIn('Z\r\nDTEND:',ics)
    def test_calendar_tba_all_day(self):
        self.event.time_tba = True
        ics, _ = calendar(self.event)
        self.assertIn('DTSTART;VALUE=DATE:',ics)
    def test_missing_date_not_exported(self):
        self.event.local_date = None
        with self.assertRaises(IntegrationError):
            calendar(self.event)
    @override_settings(TICKETMASTER_API_KEY='test')
    @patch('concerts.services.get_json')
    def test_search_is_geofenced_and_upcoming(self, fetch):
        fetch.return_value = {'page':{'totalPages':1}}
        search_events(self.profile,'Björk')
        params = fetch.call_args.kwargs['params']
        self.assertEqual((params['radius'],params['unit']),(50,'miles'))
        self.assertEqual(len(params['geoPoint']),8)
        self.assertNotIn('postalCode',params)
        self.assertIn('startDateTime',params)
    def test_event_handles_missing_prices_and_dates(self):
        e = save_event({'id':'tba','name':'Unannounced concert'})
        self.assertIsNone(e.local_date)
        self.assertIsNone(e.starts_at)
    @override_settings(CREDENTIAL_KEY=Fernet.generate_key().decode())
    @patch('concerts.services.get_json')
    def test_jellyfin_pagination_dedup_and_suppression(self, fetch):
        self.profile.jellyfin_url = 'http://jellyfin:8096'
        self.profile.jellyfin_token = encrypt('private-key')
        self.profile.save()
        Artist.objects.create(user=self.user,name='Björk',key='björk',suppressed=True)
        fetch.side_effect = [{'Items':[{'Name':'Björk'}],'TotalRecordCount':2},{'Items':[{'Name':'Radiohead'}],'TotalRecordCount':2},{'Items':[{'Name':'RADIOHEAD'}],'TotalRecordCount':1}]
        self.assertEqual(sync_jellyfin(self.profile),1)
        self.assertEqual(Artist.objects.filter(user=self.user).count(),2)
        self.assertTrue(Artist.objects.get(key='björk').suppressed)
        self.assertEqual(fetch.call_args_list[1].kwargs['params']['StartIndex'],1)
        self.assertNotIn('private-key',self.profile.jellyfin_token)
    @override_settings(CREDENTIAL_KEY=Fernet.generate_key().decode())
    @patch('concerts.services.get_json')
    def test_jellyfin_failure_does_not_partially_import(self, fetch):
        self.profile.jellyfin_token = encrypt('private-key')
        fetch.side_effect = [{'Items':[{'Name':'New artist'}],'TotalRecordCount':2},IntegrationError('failed')]
        with self.assertRaises(IntegrationError):
            sync_jellyfin(self.profile)
        self.assertFalse(Artist.objects.filter(user=self.user).exists())
    def test_future_worker_lease_prevents_concurrent_poll(self):
        WorkerState.objects.create(name='main',heartbeat=timezone.now(),lease_until=timezone.now()+timedelta(minutes=5))
        with patch('concerts.management.commands.worker.sync_jellyfin') as provider:
            tick()
            provider.assert_not_called()
    def test_announced_placeholder_date_is_not_exported(self):
        event = save_event({'id':'date-tba','name':'Date unknown','dates':{'start':{'localDate':'2026-12-31','dateTBA':True}}})
        self.assertIsNone(event.local_date)
        with self.assertRaises(IntegrationError):
            calendar(event)
    def test_changing_jellyfin_host_requires_fresh_token(self):
        self.profile.jellyfin_url = 'http://original:8096'
        self.profile.jellyfin_token = 'encrypted-existing-token'
        self.profile.save()
        response = self.client.post('/settings/',{'action':'jellyfin','url':'http://different:8096','user_id':'a'*32,'token':''})
        self.assertContains(response,'Enter a token again')
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.jellyfin_url,'http://original:8096')

    def test_event_page_displays_venue_timezone(self):
        from datetime import datetime, timezone as tz
        self.event.starts_at = datetime(2026, 12, 10, 4, 0, tzinfo=tz.utc)
        self.event.timezone = 'America/Los_Angeles'
        self.event.save()
        response = self.client.get(f'/events/{self.event.pk}/')
        self.assertContains(response, '8:00 PM PST')

    @override_settings(TICKETMASTER_API_KEY='test')
    @patch('concerts.services.get_json')
    def test_search_rejects_distant_and_unknown_locations(self, fetch):
        def event_with_location(identifier, location):
            return {'id':identifier,'name':'Test','_embedded':{'venues':[{'location':location}]}}
        fetch.return_value = {'_embedded':{'events':[
            event_with_location('near',{'latitude':34.09,'longitude':-118.40}),
            event_with_location('far',{'latitude':40.71,'longitude':-74.0}),
            event_with_location('missing',{}),
        ]}}
        events, _ = search_events(self.profile)
        self.assertEqual([e.external_id for e in events],['near'])
    def test_geohash_known_reference(self):
        from .services import geohash
        self.assertEqual(geohash(42.6,-5.6,5),'ezs42')
    @patch('concerts.services.get_json')
    def test_zip_lookup_caches_coordinates(self, fetch):
        from .services import zip_location
        fetch.return_value = {'places':[{'latitude':'40.75','longitude':'-73.99'}]}
        self.assertEqual(zip_location('10001'),(40.75,-73.99))
        self.assertEqual(zip_location('10001'),(40.75,-73.99))
        self.assertEqual(fetch.call_count,1)

    def test_price_monitoring_is_removed(self):
        self.assertEqual(self.client.get('/watches/').status_code,404)
        self.assertEqual(self.client.post(f'/events/{self.event.pk}/',{'max_price':20}).status_code,405)
        response = self.client.get(f'/events/{self.event.pk}/')
        self.assertNotContains(response,'Max price')
        self.assertNotContains(response,'Keep an eye on tickets')
        self.assertContains(response,'calendar')
        self.assertNotContains(self.client.get('/settings/'),'Email delivery')

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_worker_only_runs_remaining_integrations(self):
        from .models import CalendarSubscription
        self.profile.jellyfin_token = 'test-encrypted-token'
        self.profile.save()
        subscription = CalendarSubscription.objects.create(user=self.user)
        with patch('concerts.management.commands.worker.sync_jellyfin') as sync, patch('concerts.subscriptions.refresh_subscription') as refresh:
            tick()
        sync.assert_called_once()
        self.assertEqual(refresh.call_args.args[0],subscription)
        self.assertEqual(len(mail.outbox),0)
