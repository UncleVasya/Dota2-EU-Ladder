from app.ladder.views import PlayerList, PlayersSuccessful, MatchList, LadderStats, LobbyStatus
from app.ladder.views import PlayerOverview, PlayerScores, PlayerTeammates, PlayerOpponents
from app.ladder.views import PlayerAutocomplete
from django.conf.urls import url


urlpatterns = [
    url(r'^players/$', PlayerList.as_view(), name='player-list'),
    url(r'^players-successful/$', PlayersSuccessful.as_view(), name='player-list-score'),

    url(r'^players/(?P<slug>[-\w]+)/$', PlayerOverview.as_view(), name='player-overview'),
    url(r'^players/(?P<slug>[-\w]+)/scores/$', PlayerScores.as_view(), name='player-scores'),
    url(r'^players/(?P<slug>[-\w]+)/teammates/$', PlayerTeammates.as_view(), name='player-teammates'),
    url(r'^players/(?P<slug>[-\w]+)/opponents/$', PlayerOpponents.as_view(), name='player-opponents'),

    url(r'player-autocomplete', PlayerAutocomplete.as_view(), name='player-autocomplete'),

    url(r'^matches/$', MatchList.as_view(), name='match-list'),
    url(r'^stats/$', LadderStats.as_view(), name='stats'),
    url(r'^lobby-status/$', LobbyStatus.as_view(), name='lobby-status')
]