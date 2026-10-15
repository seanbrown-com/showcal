from datetime import date, datetime, timezone
from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from .models import Profile, Event, Artist, ZipLocation, CalendarSubscription
from .subscriptions import horizon, refresh_subscription, subscription_events, render_feed
from .services import IntegrationError

@override_settings(TICKETMASTER_API_KEY='test', PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class SubscriptionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('calendar-test')
        Profile.objects.create(user=self.user,zip_code='98101')
        ZipLocation.objects.create(zip_code='98101',latitude=47.6,longitude=-122.3)
        Artist.objects.create(user=self.user,name='Band',key='band')
        self.event = Event.objects.create(external_id='test-calendar',title='Band, live; now',
            local_date=date(2026,12,6),starts_at=datetime(2026,12,7,3,tzinfo=timezone.utc),
            latitude=47.6,longitude=-122.3,artists=[{'name':'Band'}],timezone='America/Los_Angeles')
        self.subscription = CalendarSubscription.objects.create(user=self.user)
        self.subscription.events.add(self.event)
        self.now = patch('django.utils.timezone.now',return_value=datetime(2026,10,9,20,tzinfo=timezone.utc))
        self.now.start()
        self.addCleanup(self.now.stop)
        self.url = f'/calendars/{self.subscription.token}/concerts.ics'

    def test_horizon_clamps_month_end(self):
        self.assertEqual(horizon(date(2026,8,31)),(date(2026,8,31),date(2027,2,28)))
        self.assertEqual(horizon()[1],date(2027,4,9))

    def test_private_feed_requires_token_but_not_login_and_is_stable(self):
        self.subscription.refreshed_at = datetime(2026,10,9,tzinfo=timezone.utc)
        self.subscription.save()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code,200)
        body = response.content.decode()
        self.assertIn('UID:test-calendar@ticket-watch',body)
        self.assertIn('DTSTART:20261207T030000Z',body)
        self.assertIn('SUMMARY:Band\\, live\\; now',body)
        self.assertEqual(body.count('BEGIN:VCALENDAR'),1)
        self.assertEqual(body.count('BEGIN:VEVENT'),1)
        self.assertEqual(response.content,self.client.get(self.url).content)
        self.assertEqual(self.client.get('/calendars/00000000-0000-0000-0000-000000000000/concerts.ics').status_code,404)

    def test_pending_returns_retry_not_empty_calendar(self):
        self.assertEqual(self.client.get(self.url).status_code,503)

    @patch('concerts.subscriptions.search_month_events')
    def test_refresh_all_months_and_preserve_snapshot_on_failure(self, search):
        search.return_value = ([self.event],False)
        refresh_subscription(self.subscription)
        self.assertEqual(search.call_count,7)
        self.assertEqual(search.call_args_list[0].args[2],date(2026,10,1))
        self.assertEqual(search.call_args_list[-1].args[2],date(2027,4,1))
        stamp = self.subscription.refreshed_at
        search.side_effect = IntegrationError('Unavailable')
        refresh_subscription(self.subscription)
        self.assertEqual(list(self.subscription.events.all()),[self.event])
        self.assertEqual(self.subscription.refreshed_at,stamp)
        self.assertEqual(self.subscription.error,'Unavailable')

    def test_filters_favorites_radius_dates_cancellations(self):
        self.assertEqual(subscription_events(self.subscription),[self.event])
        for field, value in [('local_date',date(2027,4,10)),('latitude',30),('status','cancelled'),
                             ('artists',[{'name':'Other'}]),('latitude',None)]:
            old = getattr(self.event,field)
            Event.objects.filter(pk=self.event.pk).update(**{field:value})
            self.assertEqual(subscription_events(self.subscription),[])
            Event.objects.filter(pk=self.event.pk).update(**{field:old})
        Artist.objects.update(suppressed=True)
        self.assertEqual(subscription_events(self.subscription),[])

    def test_rotate_disable_and_owner_isolation(self):
        self.client.force_login(self.user)
        self.assertContains(self.client.get('/calendar/'),'Private subscription URL')
        self.client.post('/calendar/',{'action':'rotate'})
        self.assertEqual(self.client.get(self.url).status_code,404)
        other = User.objects.create_user('other')
        Profile.objects.create(user=other,zip_code='98101')
        self.client.force_login(other)
        self.assertNotContains(self.client.get('/calendar/'),str(self.subscription.token))
        self.client.post('/calendar/',{'action':'disable'})
        self.assertTrue(CalendarSubscription.objects.filter(user=self.user).exists())
        self.client.force_login(self.user)
        self.client.post('/calendar/',{'action':'disable'})
        self.assertFalse(CalendarSubscription.objects.filter(user=self.user).exists())

    def test_date_only_event_and_utf8_folding(self):
        self.event.starts_at = None
        self.event.title = '音楽'*100
        output = render_feed([self.event])
        self.assertIn('DTSTART;VALUE=DATE:20261206',output)
        self.assertTrue(all(len(line.encode()) <= 75 for line in output.split('\r\n')))
