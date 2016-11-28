from app.ladder.views import PlayerList
from django.conf.urls import url


urlpatterns = [
    url(r'^players/$', PlayerList.as_view(), name='player-list'),
]