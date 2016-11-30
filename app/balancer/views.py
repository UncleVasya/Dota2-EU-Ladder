from app.balancer.balancer import balance_teams
from app.balancer.forms import BalancerForm, BalancerCustomForm
from app.balancer.models import BalanceResult
from django.core.paginator import PageNotAnInteger
from django.core.urlresolvers import reverse
from django.http import Http404
from django.views.generic import FormView, DetailView
from pure_pagination import Paginator


class BalancerInput(FormView):
    form_class = BalancerForm
    template_name = 'balancer/balancer-input.html'

    def form_valid(self, form):
        players = [(p.name, p.mmr) for p in form.cleaned_data.values()]

        # balance teams and save result
        answers = balance_teams(players)
        self.result = BalanceResult.objects.create(
            answers=answers
        )

        return super(BalancerInput, self).form_valid(form)

    def get_success_url(self):
        return reverse('balancer:balancer-result', args=(self.result.id,))


class BalancerInputCustom(FormView):
    form_class = BalancerCustomForm
    template_name = 'balancer/balancer-input-custom.html'

    def form_valid(self, form):
        players = [form.cleaned_data['player_%s' % i] for i in xrange(1, 11)]
        mmrs = [form.cleaned_data['MMR_%s' % i] for i in xrange(1, 11)]

        # balance teams and save result
        mmr_exponent = 3
        answers = balance_teams(zip(players, mmrs), mmr_exponent)
        self.result = BalanceResult.objects.create(
            answers=answers
        )

        return super(BalancerInputCustom, self).form_valid(form)

    def get_success_url(self):
        return reverse('balancer:balancer-result', args=(self.result.id,))


class BalancerResult(DetailView):
    model = BalanceResult
    template_name = 'balancer/balancer-result.html'
    context_object_name = 'result'

    def get_context_data(self, **kwargs):
        context = super(BalancerResult, self).get_context_data(**kwargs)

        # paginate
        page_num = self.request.GET.get('page', 1)
        try:
            page = Paginator(context['result'].answers, 1, request=self.request).page(page_num)
        except PageNotAnInteger:
            raise Http404

        result = context['result'] = page.object_list[0]

        # TODO: make a result.mmr_exponent DB field,
        # TODO: make an Answer model
        mmr_exponent = 3

        players = [p for team in result['teams'] for p in team['players']]
        mmr_max = max([player[1] ** mmr_exponent for player in players])

        for team in result['teams']:
            for i, player in enumerate(team['players']):
                mmr_percent = float(player[1] ** mmr_exponent) / mmr_max * 100
                team['players'][i] = {
                    'name': player[0],
                    'mmr': player[1],
                    'mmr_percent': mmr_percent
                }
                print player

        context.update({
            'pagination': page,
        })

        return context