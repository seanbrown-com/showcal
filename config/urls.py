from django.urls import path
from django.contrib.auth import views as auth
from concerts import views
urlpatterns = [
    path('calendar/',views.calendar_settings,name='calendar_settings'),
    path('calendars/<uuid:token>/concerts.ics',views.calendar_feed,name='calendar_feed'),
    path('',views.home,name='home'),path('signup/',views.signup,name='signup'),
    path('login/',auth.LoginView.as_view(),name='login'),path('logout/',auth.LogoutView.as_view(),name='logout'),
    path('events/<int:pk>/',views.detail,name='detail'),path('events/<int:pk>/calendar.ics',views.calendar_download,name='calendar'),path('events/<int:pk>/facebook/',views.facebook,name='facebook'),
    path('artists/',views.artists,name='artists'),path('artists/add/',views.add_artist,name='add_artist'),path('artists/<int:pk>/remove/',views.remove_artist,name='remove_artist'),
    path('settings/',views.profile,name='profile'),path('settings/jellyfin/sync/',views.jellyfin_sync,name='jellyfin_sync'),path('settings/jellyfin/disconnect/',views.jellyfin_disconnect,name='jellyfin_disconnect'),path('health/',views.health,name='health')]
