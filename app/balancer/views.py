from app.balancer.balancer import balance_teams
from app.balancer.forms import BalancerForm
from app.balancer.models import BalanceResult
from django.core.urlresolvers import reverse
from django.views.generic import FormView, DetailView


class BalancerInput(FormView):
    form_class = BalancerForm
    template_name = 'balancer/balancer-input.html'

    def form_valid(self, form):
        players = [form.cleaned_data['player_%s' % i] for i in xrange(1, 11)]
        mmrs = [form.cleaned_data['MMR_%s' % i] for i in xrange(1, 11)]

        # balance teams and save result
        answers = balance_teams(zip(players, mmrs))
        self.result = BalanceResult.objects.create(
            answers=answers
        )

        return super(BalancerInput, self).form_valid(form)

    def get_success_url(self):
        return reverse('balancer:balancer-result', args=(self.result.id,))


class BalancerResult(DetailView):
    model = BalanceResult
    template_name = 'balancer/balancer-result.html'
    context_object_name = 'result'