from django.contrib.auth.mixins import PermissionRequiredMixin
from app.balancer.balancer import balance_teams, role_names
from app.balancer.forms import BalancerForm, BalancerCustomForm
from app.balancer.managers import BalanceResultManager, BalanceAnswerManager
from app.balancer.models import BalanceResult, BalanceAnswer
from app.ladder.models import Player, Match, MatchPlayer
from django.core.paginator import PageNotAnInteger
from django.urls import reverse_lazy, reverse
from django.db import transaction
from django.http import Http404, HttpResponseBadRequest
from django.views.generic import FormView, DetailView, RedirectView
from app.ladder.managers import MatchManager
from pure_pagination import Paginator


class BalancerInput(FormView):
    form_class = BalancerForm
    template_name = 'balancer/balancer-input.html'

    def form_valid(self, form):
        players = list(form.cleaned_data.values())

        self.result = BalanceResultManager.balance_teams(players)

        return super(BalancerInput, self).form_valid(form)

    def get_success_url(self):
        return reverse('balancer:balancer-result', args=(self.result.id,))


class BalancerInputCustom(FormView):
    form_class = BalancerCustomForm
    template_name = 'balancer/balancer-input-custom.html'

    def form_valid(self, form):
        players = [form.cleaned_data['player_%s' % i] for i in range(1, 11)]
        mmrs = [form.cleaned_data['MMR_%s' % i] for i in range(1, 11)]

        self.result = BalanceResultManager.balance_teams(zip(players, mmrs))

        return super(BalancerInputCustom, self).form_valid(form)

    def get_success_url(self):
        return reverse('balancer:balancer-result', args=(self.result.id,))


class RecordMatch(FormView):
    form_class = BalancerForm
    template_name = 'balancer/record-match.html'

    def form_valid(self, form):
        players = list(form.cleaned_data.values())

        radiant = [(p.name, p.ladder_mmr) for p in players[:5]]
        dire = [(p.name, p.ladder_mmr) for p in players[5:]]

        self.answer = BalanceAnswerManager.balance_custom([radiant, dire])

        return super(RecordMatch, self).form_valid(form)

    def get_success_url(self):
        return reverse('balancer:balancer-answer', args=(self.answer.id,))


class BalancerResult(DetailView):
    model = BalanceResult
    template_name = 'balancer/balancer-result.html'
    context_object_name = 'result'

    def get_context_data(self, **kwargs):
        context = super(BalancerResult, self).get_context_data(**kwargs)

        # paginate
        page_num = self.request.GET.get('page', 1)
        try:
            answers = context['result'].answers.all()
            page = Paginator(answers, 1, request=self.request).page(page_num)
        except PageNotAnInteger:
            raise Http404

        answer = page.object_list[0]
        mmr_exponent = answer.result.mmr_exponent

        players = [p for team in answer.teams for p in team['players']]
        mmr_max = max([player[1] ** mmr_exponent for player in players])

        for team in answer.teams:
            for i, player in enumerate(team['players']):
                mmr_percent = float(player[1] ** mmr_exponent) / mmr_max * 100
                team['players'][i] = {
                    'name': player[0],
                    'mmr': player[1],
                    'mmr_percent': mmr_percent,
                    'role_score': team['role_score'][i],
                }

        context.update({
            'answer': answer,
            'role_names': role_names,
            'pagination': page,
        })

        return context


class BalancerAnswer(DetailView):
    model = BalanceAnswer
    # TODO make separate balancer-answer.html template and include it in BalancerResult page
    template_name = 'balancer/balancer-result.html'
    context_object_name = 'answer'

    def get_context_data(self, **kwargs):
        context = super(BalancerAnswer, self).get_context_data(**kwargs)
        answer = context['answer']

        # TODO: move mmr_exponent field from BalanceResult to BalanceAnswer model
        # TODO: also this code repeats from BalancerResult view. Move to separate func?
        mmr_exponent = 3

        players = [p for team in answer.teams for p in team['players']]
        mmr_max = max([player[1] ** mmr_exponent for player in players])

        for team in answer.teams:
            for i, player in enumerate(team['players']):
                try:
                    slug = Player.objects.get(name=player[0]).slug
                except Player.DoesNotExist:
                    slug = ''  # player was renamed since this balance

                mmr_percent = float(player[1] ** mmr_exponent) / mmr_max * 100
                team['players'][i] = {
                    'name': player[0],
                    'mmr': player[1],
                    'mmr_percent': mmr_percent,
                    'role_score': team['role_score'][i] if team.get('role_score') else None,
                    'slug': slug,
                }

        context.update({
            'answer': answer,
            'role_names': role_names,
        })

        return context


class MatchCreate(PermissionRequiredMixin, RedirectView):
    url = reverse_lazy('ladder:player-list')
    permission_required = 'ladder.add_match'

    def get(self, request, *args, **kwargs):
        try:
            answer = BalanceAnswer.objects.get(id=kwargs['pk'])
        except BalanceAnswer.DoesNotExist:
            return HttpResponseBadRequest(request)

        if hasattr(answer, 'match'):
            # we already created a match from this BalanceAnswer
            return super(MatchCreate, self).get(request, *args, **kwargs)

        # check that players from balance exist
        # (we don't allow CustomBalance results here)
        players = [p[0] for t in answer.teams for p in t['players']]
        players = Player.objects.filter(name__in=players)

        if len(players) < 10:
            return HttpResponseBadRequest(request)

        MatchManager.record_balance(answer, int(kwargs['winner']))

        return super(MatchCreate, self).get(request, *args, **kwargs)


class MatchDelete(PermissionRequiredMixin, RedirectView):
    pattern_name = 'balancer:balancer-answer'
    permission_required = 'ladder.delete_match'

    def get(self, request, *args, **kwargs):
        try:
            answer = BalanceAnswer.objects.get(id=kwargs['pk'])
        except BalanceAnswer.DoesNotExist:
            return HttpResponseBadRequest(request)

        if not hasattr(answer, 'match'):
            # no match to delete
            return super(MatchDelete, self).get(request, *args, **kwargs)

        answer.match.delete()

        return super(MatchDelete, self).get(request, *args, **kwargs)
