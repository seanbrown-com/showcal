from django.conf import settings
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator, RegexValidator

class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    zip_code = models.CharField(max_length=5, validators=[RegexValidator(r'^\d{5}$', 'Enter a five-digit US ZIP code.')])
    radius = models.PositiveIntegerField(default=50, validators=[MinValueValidator(1),MaxValueValidator(250)])
    jellyfin_url = models.URLField(blank=True)
    jellyfin_user_id = models.CharField(max_length=64, blank=True)
    jellyfin_token = models.TextField(blank=True)
    last_sync = models.DateTimeField(null=True, blank=True)
    sync_error = models.CharField(max_length=250, blank=True)
    sync_attempt = models.DateTimeField(null=True, blank=True)

class Artist(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=250)
    key = models.CharField(max_length=250)
    source = models.CharField(max_length=20, default='manual')
    external_id = models.CharField(max_length=150, blank=True)
    suppressed = models.BooleanField(default=False)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['user','key'],name='unique_user_artist')]
        ordering = ['name']

class Event(models.Model):
    source = models.CharField(max_length=20, default='ticketmaster', choices=[('ticketmaster','Ticketmaster'),('axs','AXS')])
    external_id = models.CharField(max_length=150, unique=True)
    title = models.CharField(max_length=500)
    venue = models.CharField(max_length=300, blank=True)
    city = models.CharField(max_length=200, blank=True)
    latitude = models.FloatField(null=True, blank=True, validators=[MinValueValidator(-90), MaxValueValidator(90)])
    longitude = models.FloatField(null=True, blank=True, validators=[MinValueValidator(-180), MaxValueValidator(180)])
    starts_at = models.DateTimeField(null=True)
    local_date = models.DateField(null=True)
    timezone = models.CharField(max_length=100, default='UTC')
    time_tba = models.BooleanField(default=False)
    url = models.URLField(max_length=2000, blank=True)
    image = models.URLField(max_length=2000, blank=True)
    artists = models.JSONField(default=list)
    status = models.CharField(max_length=50, default='onsale')
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ['local_date','starts_at']



class WorkerState(models.Model):
    name = models.CharField(max_length=30, unique=True)
    heartbeat = models.DateTimeField()
    lease_until = models.DateTimeField(null=True)

class ZipLocation(models.Model):
    zip_code = models.CharField(max_length=5, primary_key=True)
    latitude = models.FloatField()
    longitude = models.FloatField()



class CalendarSubscription(models.Model):
    import uuid
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    events = models.ManyToManyField(Event, blank=True)
    refreshed_at = models.DateTimeField(null=True, blank=True)
    attempted_at = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=300, blank=True)
