from django.conf.urls import url

from app.balancer.views import BalancerInput, BalancerResult

urlpatterns = [
    url(r'^$', BalancerInput.as_view(), name='balancer-input'),
    url(r'^results/(?P<pk>[0-9]+)/$', BalancerResult.as_view(), name='balancer-result'),
]