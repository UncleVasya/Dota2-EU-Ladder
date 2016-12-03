from app.ladder.views import PlayerList, PlayerAutocomplete, PlayerOverview
from django.conf.urls import url


urlpatterns = [
    url(r'^players/$', PlayerList.as_view(), name='player-list'),
    url(r'^players/(?P<slug>[-\w]+)/$', PlayerOverview.as_view(), name='player-overview'),

    url(r'player-autocomplete', PlayerAutocomplete.as_view(), name='player-autocomplete')
]