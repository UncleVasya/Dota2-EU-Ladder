from app.ladder.views import PlayerList, PlayersSuccessful
from app.ladder.views import PlayerOverview, PlayerAllies
from app.ladder.views import PlayerAutocomplete
from django.conf.urls import url


urlpatterns = [
    url(r'^players/$', PlayerList.as_view(), name='player-list'),
    url(r'^players-successful/$', PlayersSuccessful.as_view(), name='player-list-score'),

    url(r'^players/(?P<slug>[-\w]+)/$', PlayerOverview.as_view(), name='player-overview'),
    url(r'^players/(?P<slug>[-\w]+)/teammates/$', PlayerAllies.as_view(), name='player-teammates'),

    url(r'player-autocomplete', PlayerAutocomplete.as_view(), name='player-autocomplete')
]