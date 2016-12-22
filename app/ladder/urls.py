from app.ladder.views import PlayerList, PlayersSuccessful
from app.ladder.views import PlayerOverview, PlayerTeammates, PlayerOpponents
from app.ladder.views import PlayerAutocomplete
from django.conf.urls import url


urlpatterns = [
    url(r'^players/$', PlayerList.as_view(), name='player-list'),
    url(r'^players-successful/$', PlayersSuccessful.as_view(), name='player-list-score'),

    url(r'^players/(?P<slug>[-\w]+)/$', PlayerOverview.as_view(), name='player-overview'),
    url(r'^players/(?P<slug>[-\w]+)/teammates/$', PlayerTeammates.as_view(), name='player-teammates'),
    url(r'^players/(?P<slug>[-\w]+)/opponents/$', PlayerOpponents.as_view(), name='player-opponents'),

    url(r'player-autocomplete', PlayerAutocomplete.as_view(), name='player-autocomplete')
]