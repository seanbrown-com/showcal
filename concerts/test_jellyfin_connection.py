from unittest.mock import patch
from cryptography.fernet import Fernet
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from .forms import JellyfinForm
from .models import Profile
from .services import IntegrationError, encrypt, decrypt_jellyfin_token, resolve_jellyfin_user

USER_ID = '1234567890abcdef1234567890abcdef'
SERVER = 'https://jellyfin.example.test'

@override_settings(CREDENTIAL_KEY=Fernet.generate_key().decode(),PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class JellyfinConnectionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('listener',password='test')
        self.profile = Profile.objects.create(user=self.user,zip_code='98101')
        self.client.force_login(self.user)

    def submit(self,**kwargs):
        return self.client.post('/settings/',{'action':'jellyfin','url':SERVER,'user_id':'ticketwatch','token':'private-token',**kwargs})

    def test_username_passes_form_validation(self):
        form = JellyfinForm({'url':SERVER,'user_id':'ticketwatch','token':'test-token'})
        self.assertTrue(form.is_valid(),form.errors)

    @patch('concerts.services.get_json')
    def test_username_is_resolved_and_token_encrypted(self, fetch):
        fetch.return_value = [{'Name':'TicketWatch','Id':USER_ID}]
        response = self.submit()
        self.assertEqual(response.status_code,302)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.jellyfin_user_id,USER_ID)
        self.assertEqual(decrypt_jellyfin_token(self.profile.jellyfin_token),'private-token')
        self.assertNotEqual(self.profile.jellyfin_token,'private-token')
        fetch.assert_called_once_with(SERVER+'/Users',headers={'X-Emby-Token':'private-token'})

    @patch('concerts.services.get_json')
    def test_direct_uuid_verified_without_listing_users(self, fetch):
        fetch.return_value = {'Name':'ticketwatch','Id':USER_ID}
        self.assertEqual(resolve_jellyfin_user(SERVER,'12345678-90ab-cdef-1234-567890abcdef','token'),USER_ID)
        fetch.assert_called_once_with(SERVER+'/Users/'+USER_ID,headers={'X-Emby-Token':'token'})

    @patch('concerts.services.get_json')
    def test_unknown_username_leaves_existing_connection_untouched(self, fetch):
        self.profile.jellyfin_url = SERVER
        self.profile.jellyfin_user_id = USER_ID
        original = self.profile.jellyfin_token = encrypt('old-token')
        self.profile.save()
        fetch.return_value = []
        response = self.submit()
        self.assertContains(response,'Check the spelling')
        self.assertNotContains(response,'private-token')
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.jellyfin_token,original)
        self.assertEqual(self.profile.jellyfin_user_id,USER_ID)

    @patch('concerts.services.get_json')
    def test_saved_token_can_be_reused_on_same_server(self, fetch):
        self.profile.jellyfin_url = SERVER
        self.profile.jellyfin_token = encrypt('old-token')
        self.profile.save()
        fetch.return_value = [{'Name':'ticketwatch','Id':USER_ID}]
        self.assertEqual(self.submit(token='').status_code,302)
        fetch.assert_called_once_with(SERVER+'/Users',headers={'X-Emby-Token':'old-token'})

    @patch('concerts.services.get_json')
    def test_first_connection_requires_token_before_network_request(self, fetch):
        self.assertContains(self.submit(token=''),'Enter a Jellyfin API key or access token.')
        fetch.assert_not_called()

    @patch('concerts.services.get_json')
    def test_old_token_is_never_sent_to_changed_host(self, fetch):
        self.profile.jellyfin_url = SERVER
        self.profile.jellyfin_token = encrypt('old-token')
        self.profile.save()
        self.assertContains(self.submit(url='https://different.example.test',token=''),'Enter a token again')
        fetch.assert_not_called()

    @patch('concerts.services.get_json')
    def test_permission_failure_has_actionable_sanitized_message(self, fetch):
        fetch.side_effect = IntegrationError('HTTP 403 secret-details')
        response = self.submit()
        self.assertContains(response,'token permissions')
        self.assertContains(response,'internal user ID')
        self.assertNotContains(response,'secret-details')
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.jellyfin_token,'')

    @patch('concerts.services.get_json')
    def test_ambiguous_or_invalid_server_user_is_rejected(self, fetch):
        for data in [[{'Name':'ticketwatch','Id':'invalid'}],[{'Name':'ticketwatch','Id':USER_ID}]*2,{'Items':[]}]:
            fetch.return_value = data
            with self.subTest(data=data), self.assertRaises(IntegrationError):
                resolve_jellyfin_user(SERVER,'ticketwatch','token')

    @patch('concerts.services.get_json')
    def test_wrong_user_id_response_is_rejected(self, fetch):
        fetch.return_value = {'Id':'a'*32}
        with self.assertRaisesMessage(IntegrationError,'different user'):
            resolve_jellyfin_user(SERVER,USER_ID,'token')
