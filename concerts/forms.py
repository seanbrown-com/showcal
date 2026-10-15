from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from .models import Profile

class SignupForm(UserCreationForm):
    email = forms.EmailField()
    zip_code = forms.RegexField(r'^\d{5}$', label='US ZIP code', max_length=5)
    radius = forms.IntegerField(initial=50, min_value=1, max_value=250, label='Search radius (miles)')
    class Meta:
        model = User
        fields = ['username','email','password1','password2','zip_code','radius']
    def clean_email(self):
        email = self.cleaned_data['email'].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('An account already uses this email address.')
        return email

class ProfileForm(forms.ModelForm):
    radius = forms.IntegerField(min_value=1, max_value=250, label='Search radius (miles)')
    class Meta:
        model = Profile
        fields = ['zip_code','radius']
        labels = {'zip_code':'US ZIP code','radius':'Search radius (miles)'}

class JellyfinForm(forms.Form):
    url = forms.CharField(max_length=200, widget=forms.URLInput, label='Jellyfin server URL', help_text='Must be reachable from the app and worker. A private network address is supported.')
    user_id = forms.CharField(max_length=255, label='Jellyfin username or user ID', help_text='Enter your username (for example, showcal) or the internal user ID. We look up usernames using the token below.')
    token = forms.CharField(widget=forms.PasswordInput, required=False, label='API key or access token', help_text='Required for a new connection. Use a Jellyfin API key or access token, not your password. Leave blank only to keep a previously saved token.')

    def clean_url(self):
        from urllib.parse import urlparse
        import re
        value = self.cleaned_data['url']
        try:
            parsed = urlparse(value)
            port = parsed.port
            valid = parsed.scheme in {'http','https'} and parsed.hostname and re.fullmatch(r'[A-Za-z0-9._:-]+', parsed.hostname) and not re.search(r'\s', value)
        except ValueError:
            valid = False
        if not valid:
            raise forms.ValidationError('Enter an HTTP(S) Jellyfin URL, including its hostname or IP address.')
        return value
