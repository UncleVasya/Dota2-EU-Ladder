from django.conf.urls import url

from app.balancer.views import BalancerView

urlpatterns = [
    url(r'^$', BalancerView.as_view(), name='balancer'),
]