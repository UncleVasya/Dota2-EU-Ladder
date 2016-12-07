from django.contrib.auth.mixins import PermissionRequiredMixin
from app.balancer import models
from app.balancer.balancer import balance_teams
from app.balancer.forms import BalancerForm, BalancerCustomForm
from app.balancer.models import BalanceResult, BalanceAnswer
from app.ladder.models import Player, Match, MatchPlayer
from django.core.paginator import PageNotAnInteger
from django.core.urlresolvers import reverse_lazy, reverse
from django.db import transaction
from django.http import Http404, HttpResponseBadRequest
from django.views.generic import FormView, DetailView, RedirectView
from app.ladder.managers import MatchManager
from pure_pagination import Paginator


class BalancerInput(FormView):
    form_class = BalancerForm
    template_name = 'balancer/balancer-input.html'

    def form_valid(self, form):
        players = [(p.name, p.ladder_mmr) for p in form.cleaned_data.values()]

        # balance teams and save result
        mmr_exponent = 3
        answers = balance_teams(players, mmr_exponent)

        with transaction.atomic():
            self.result = models.BalanceResult.objects.create(mmr_exponent=mmr_exponent)
            for answer in answers:
                models.BalanceAnswer.objects.create(
                    teams=answer['teams'],
                    mmr_diff=answer['mmr_diff'],
                    mmr_diff_exp=answer['mmr_diff_exp'],
                    result=self.result
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

        self.result = BalanceResult.objects.create(mmr_exponent=mmr_exponent)
        for answer in answers:
            BalanceAnswer.objects.create(
                teams=answer['teams'],
                mmr_diff=answer['mmr_diff'],
                mmr_diff_exp=answer['mmr_diff_exp'],
                result=self.result
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

        answer = self.kwargs.get('answer', None)
        page = None

        # TODO: make separate BalanceAnswer view
        if answer is not None:
            try:
                answer = BalanceAnswer.objects.get(id=answer)
            except BalanceAnswer.DoesNotExist:
                raise Http404
        else:
            # paginate
            page_num = self.request.GET.get('page', 1)
            try:
                answers = context['result'].answers.all()
                page = Paginator(answers, 1, request=self.request).page(page_num)
            except PageNotAnInteger:
                raise Http404

            answer = page.object_list[0]

        # TODO: make a result.mmr_exponent DB field,
        # TODO: make an Answer model
        mmr_exponent = answer.result.mmr_exponent

        players = [p for team in answer.teams for p in team['players']]
        mmr_max = max([player[1] ** mmr_exponent for player in players])

        for team in answer.teams:
            for i, player in enumerate(team['players']):
                mmr_percent = float(player[1] ** mmr_exponent) / mmr_max * 100
                team['players'][i] = {
                    'name': player[0],
                    'mmr': player[1],
                    'mmr_percent': mmr_percent
                }

        context.update({
            'answer': answer,
            'pagination': page,
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

        with transaction.atomic():
            match = Match.objects.create(
                winner=int(kwargs['winner']),
                balance=answer,
            )

            for i, team in enumerate(answer.teams):
                for player in team['players']:
                    player = next(p for p in players if p.name == player[0])

                    MatchPlayer.objects.create(
                        match=match,
                        player=player,
                        team=i
                    )

            MatchManager.add_scores(match)
            Player.objects.update_ranks()

        return super(MatchCreate, self).get(request, *args, **kwargs)
