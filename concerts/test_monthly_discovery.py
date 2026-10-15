from datetime import date, datetime, timezone
from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from .models import Profile, Event, ZipLocation, Artist
from .services import search_month_events, save_event

NOW = datetime(2026,10,9,20,tzinfo=timezone.utc)

@override_settings(TICKETMASTER_API_KEY='test',PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class MonthlyDiscoveryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('monthly-test')
        self.profile = Profile.objects.create(user=self.user,zip_code='98101')
        ZipLocation.objects.create(zip_code='98101',latitude=47.6,longitude=-122.3)
        self.client.force_login(self.user)

    @patch('concerts.views.timezone.localdate',return_value=date(2026,10,9))
    @patch('concerts.views.search_month_events',return_value=([],False))
    def test_defaults_to_current_month_and_preserves_query_on_navigation(self, search, today):
        response = self.client.get('/?q=Kacey&favorites=1')
        search.assert_called_once_with(self.profile,'Kacey',date(2026,10,1))
        self.assertContains(response,'October 1–31, 2026')
        self.assertContains(response,'November 2026 →')
        self.assertContains(response,'month=2026-11&amp;favorites=1')
        self.assertNotContains(response,'Next shows')
        self.assertIsNone(response.context['previous_month'])

    @patch('concerts.views.timezone.localdate',return_value=date(2026,10,9))
    @patch('concerts.views.search_month_events',return_value=([],False))
    def test_empty_month_still_has_both_month_links(self, search, today):
        response = self.client.get('/?month=2026-11&q=Silverstein')
        self.assertContains(response,'November 1–30, 2026')
        self.assertContains(response,'← October 2026')
        self.assertContains(response,'December 2026 →')
        self.assertContains(response,'name="month" value="2026-11"')
        self.assertContains(response,'No upcoming shows found for November')

    @patch('concerts.views.timezone.localdate',return_value=date(2026,10,9))
    @patch('concerts.views.search_month_events',return_value=([],False))
    def test_year_rollover_leap_month_and_invalid_input(self, search, today):
        response = self.client.get('/?month=2026-12')
        self.assertEqual(response.context['next_month'],date(2027,1,1))
        self.assertContains(self.client.get('/?month=2028-02'),'February 1–29, 2028')
        for month in ['bad','2026-13','2026-09','9999-12']:
            self.assertEqual(self.client.get('/',{'month':month}).context['month_start'],date(2026,10,1))

    @patch('concerts.views.timezone.localdate',return_value=date(2026,10,9))
    @patch('concerts.views.search_month_events')
    def test_card_includes_full_date(self, search, today):
        event = Event.objects.create(external_id='monthly-card',title='October Show',local_date=date(2026,10,27),url='https://example.com/')
        search.return_value = ([event],False)
        response = self.client.get('/?favorites=0')
        self.assertContains(response,'datetime="2026-10-27"')
        self.assertContains(response,'Tue, Oct 27')
        self.assertContains(response,'compact-event-grid')

    def raw_event(self, identifier, day):
        return {'id':identifier,'name':identifier,'dates':{'start':{'localDate':day,'dateTime':day+'T20:00:00Z'}},'_embedded':{'venues':[{'location':{'latitude':'47.6','longitude':'-122.3'}}]}}

    @patch('concerts.services.timezone.now',return_value=NOW)
    @patch('concerts.services.ticketmaster')
    def test_fetches_provider_pages_and_excludes_adjacent_months(self, tm, now):
        tm.side_effect = [
            {'_embedded':{'events':[self.raw_event('first','2026-10-10'),self.raw_event('outside','2026-11-01')]},'page':{'totalPages':2}},
            {'_embedded':{'events':[self.raw_event('last','2026-10-31'),self.raw_event('first','2026-10-10')]},'page':{'totalPages':2}}
        ]
        events, limited = search_month_events(self.profile,'',date(2026,10,1))
        self.assertEqual([e.external_id for e in events],['first','last'])
        self.assertFalse(limited)
        self.assertEqual(tm.call_count,2)
        params = tm.call_args_list[0].args[1]
        self.assertEqual(params['localStartDateTime'],'2026-10-01T00:00:00,2026-10-31T23:59:59')
        self.assertEqual(params['size'],200)
        self.assertEqual(tm.call_args_list[1].args[1]['page'],1)
        self.assertEqual(params['startDateTime'],'2026-10-09T20:00:00Z')

    @patch('concerts.services.ticketmaster')
    def test_provider_limit_is_reported(self, tm):
        tm.return_value = {'page':{'totalPages':8}}
        events, limited = search_month_events(self.profile,'',date(2026,10,1))
        self.assertTrue(limited)
        self.assertEqual(tm.call_count,5)

    @patch('concerts.views.timezone.localdate',return_value=date(2026,10,9))
    @patch('concerts.views.search_month_events')
    def test_fresh_provider_dates_render_before_database_reload(self, search, today):
        for day in ['2026-10-27','2026-10-28']:
            event = save_event(self.raw_event('fresh-date',day))
            self.assertIsInstance(event.local_date,date)
            search.return_value = ([event],False)
            response = self.client.get('/?favorites=0')
            self.assertContains(response,f'datetime="{day}"')
            self.assertNotContains(response,'Date TBA')

    @patch('concerts.views.timezone.localdate',return_value=date(2026,10,9))
    @patch('concerts.views.search_month_events')
    def test_favorites_default_and_all_concerts_persists(self, search, today):
        Artist.objects.create(user=self.user,name='Favorite band',key='favorite band')
        favorite = Event.objects.create(external_id='fav',title='Favorite show',artists=[{'name':'Favorite Band'}],local_date=date(2026,10,27))
        other = Event.objects.create(external_id='other',title='Other show',local_date=date(2026,10,27))
        search.return_value = ([favorite,other],False)
        response = self.client.get('/')
        self.assertTrue(response.context['favorites_only'])
        self.assertContains(response,'Favorite show')
        self.assertNotContains(response,'Other show')
        self.assertContains(response,'month=2026-11&amp;favorites=1')
        response = self.client.get('/?favorites=0')
        self.assertFalse(response.context['favorites_only'])
        self.assertContains(response,'Other show')
        self.assertContains(response,'month=2026-11&amp;favorites=0')
        self.assertContains(response,'name="favorites" value="0"')
