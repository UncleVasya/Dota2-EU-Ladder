from django.conf.urls import url

from app.balancer.views import BalancerInput, BalancerResult, BalancerInputCustom

urlpatterns = [
    url(r'^$', BalancerInput.as_view(), name='balancer-input'),
    url(r'^results/(?P<pk>[0-9]+)/$', BalancerResult.as_view(), name='balancer-result'),

    url(r'^balancer-input-custom', BalancerInputCustom.as_view(), name='balancer-input-custom'),
]