from datetime import date, datetime, timezone
from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from .models import Artist, Event, Profile, ZipLocation
from .services import IntegrationError, saved_month_events, merge_discovery_events

@override_settings(TICKETMASTER_API_KEY='test', PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class SavedDiscoveryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('axs-test')
        self.profile = Profile.objects.create(user=self.user, zip_code='98101', radius=50)
        ZipLocation.objects.create(zip_code='98101', latitude=47.6, longitude=-122.3)
        Artist.objects.create(user=self.user, name='Father of Peace', key='father of peace')
        self.event = Event.objects.create(source='axs', external_id='axs:1496637', title='Father Of Peace',
            venue='Neumos', city='Seattle', latitude=47.61395, longitude=-122.31961,
            local_date=date(2026,12,6), starts_at=datetime(2026,12,7,3,tzinfo=timezone.utc),
            timezone='America/Los_Angeles', artists=[{'name':'Father Of Peace'}],
            url='https://www.axs.com/events/1496637/father-of-peace-tickets')
        self.client.force_login(self.user)
        self.now = patch('django.utils.timezone.now',return_value=datetime(2026,10,9,20,tzinfo=timezone.utc))
        self.now.start()
        self.addCleanup(self.now.stop)

    def test_month_search_and_radius(self):
        def find(query='', month=date(2026,12,1)):
            return saved_month_events(self.profile,query,month)
        self.assertEqual(find(),[self.event])
        self.assertEqual(find('father peace'),[self.event])
        self.assertEqual(find('Neumos'),[self.event])
        self.assertEqual(find('Silverstein'),[])
        self.assertEqual(find(month=date(2026,11,1)),[])
        for updates in [{'latitude':40.7},{'latitude':None},{'status':'cancelled'},
                        {'starts_at':datetime(2026,1,1,tzinfo=timezone.utc)}]:
            with self.subTest(updates=updates):
                original = {key:getattr(self.event,key) for key in updates}
                Event.objects.filter(pk=self.event.pk).update(**updates)
                self.assertEqual(find(),[])
                Event.objects.filter(pk=self.event.pk).update(**original)

    @patch('concerts.views.search_month_events',return_value=([],False))
    def test_favorite_appears_in_december_cards(self, search):
        response = self.client.get('/?month=2026-12&favorites=1')
        self.assertContains(response,'Father Of Peace')
        self.assertContains(response,'Sun, Dec 6')
        self.assertContains(response,'7:00 PM')
        self.assertContains(response,'YOUR ARTIST')
        self.assertNotContains(self.client.get('/?month=2026-11&favorites=1'),'Father Of Peace')
        Artist.objects.filter(user=self.user).update(suppressed=True)
        self.assertNotContains(self.client.get('/?month=2026-12&favorites=1'),'Father Of Peace')

    @patch('concerts.views.search_month_events',side_effect=IntegrationError('Provider unavailable'))
    def test_saved_cards_survive_provider_outage(self, search):
        response = self.client.get('/?month=2026-12')
        self.assertContains(response,'Father Of Peace')
        self.assertContains(response,'Provider unavailable')

    @override_settings(TICKETMASTER_API_KEY='')
    def test_saved_cards_work_without_ticketmaster_key(self):
        self.assertContains(self.client.get('/?month=2026-12'),'Father Of Peace')

    def test_duplicate_ticket_link_only_shown_once(self):
        duplicate = Event(external_id='tm-other-id',title=self.event.title,local_date=self.event.local_date,
                          starts_at=self.event.starts_at,url=self.event.url+'?tracking=1')
        self.assertEqual(merge_discovery_events([duplicate],[self.event]),[duplicate])
