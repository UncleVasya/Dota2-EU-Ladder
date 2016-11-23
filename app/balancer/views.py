from app.balancer.forms import BalancerForm
from django.views.generic import FormView


class BalancerView(FormView):
    form_class = BalancerForm
    template_name = 'balancer/balancer.html'