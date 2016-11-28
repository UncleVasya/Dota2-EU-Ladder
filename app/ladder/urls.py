from app.ladder.views import PlayerList, PlayerAutocomplete
from django.conf.urls import url


urlpatterns = [
    url(r'^players/$', PlayerList.as_view(), name='player-list'),

    url(r'player-autocomplete', PlayerAutocomplete.as_view(), name='player-autocomplete')
]