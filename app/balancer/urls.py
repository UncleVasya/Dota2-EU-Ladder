from django.conf.urls import url

from app.balancer.views import BalancerInput, BalancerResult, BalancerInputCustom, MatchCreate, BalancerAnswer, \
    MatchDelete, RecordMatch

urlpatterns = [
    url(r'^$', BalancerInput.as_view(), name='balancer-input'),
    url(r'^balancer-input-custom', BalancerInputCustom.as_view(), name='balancer-input-custom'),

    url(r'^results/(?P<pk>[0-9]+)/$', BalancerResult.as_view(),
        name='balancer-result'),
    url(r'^answers/(?P<pk>[0-9]+)/$', BalancerAnswer.as_view(),
        name='balancer-answer'),

    url(r'^answers/(?P<pk>[0-9]+)/match-create/(?P<winner>[0-1])/$', MatchCreate.as_view(),
        name='match-create'),
    url(r'^answers/(?P<pk>[0-9]+)/match-delete/$', MatchDelete.as_view(),
        name='match-delete'),

    url(r'^record-match/$', RecordMatch.as_view(), name='record-match'),
]
