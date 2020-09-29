import itertools
import random
from typing import List

from app.ladder.models import Match, Player


role_names = ['carry', 'mid', 'offlane', 'pos4', 'pos5']
role_permutations = list(itertools.permutations(role_names, 5))


def balance_teams(players, mmr_exponent=3):
    """
    Takes a list of 10 players and produces
    a list of suitable teams pairs.

    :param players: a list of players (name and MMR for each).
           Example input:

                players = [
                    ('Denden',     3000),
                    ('Uvs',        8000),
                    ('Polshy',     5000),
                    ('SMMN',       5500),
                    ('Ulafzs',     6000),

                    ('Lazy Panda', 4000),
                    ('Smile',      5000),
                    ('rawr',       3500),
                    ('Paul',       6200),
                    ('Mikel',      2400),
                ]

    :return: a list of team pairs with some meta data
    """

    team_players = len(players) // 2

    # sort players by mmr
    players.sort(key=lambda x: -x[1])

    # get all possible teams
    teams = list(itertools.combinations(players, team_players))

    # calc avg MMR for each team
    # --------------------------
    # Taking MMR exponent will make difference between high mmr players
    # to have more impact than same difference between low mmr players
    # (good for balancing).
    teams = [
        {
            'players': team,
            'mmr': sum(player[1] for player in team) // team_players,
            'mmr_exp': sum(player[1] ** mmr_exponent for player in team) // team_players
        }
        for team in teams
    ]

    # combine teams into pairs against each other
    half = len(teams) // 2
    answers = zip(teams[:half], list(reversed(teams[half:])))

    # discard answers that place top 2 or lowest 2 players in the same team
    top_players = players[:2]
    low_players = players[-2:]
    answers = [
        x for x in answers
            if len(set(x[0]['players']).intersection(top_players)) == 1 and
               len(set(x[0]['players']).intersection(low_players)) == 1
    ]

    # calc mmr differences for each pair of teams
    answers = [
        {
            'teams': random.sample(answer, len(answer)),  # assign team side randomly (Radiant or Dire)
            'mmr_diff': abs(answer[0]['mmr'] - answer[1]['mmr']),
            'mmr_diff_exp': abs(answer[0]['mmr_exp'] - answer[1]['mmr_exp'])
        }
        for answer in answers
    ]

    # sort answers by mmr difference
    answers.sort(key=lambda x: x['mmr_diff_exp'])

    return answers


def role_balance_teams(players: List[Player], mmr_exponent=3):
    def intersection(team, players):
        return len(set(team['players']).intersection(players))

    def both_teams_have(answer, players, amount):
        return intersection(answer[0], players) >= amount and \
               intersection(answer[1], players) >= amount

    def assign_best_roles(team):
        top2_mmr = team['players'][1].ladder_mmr

        role_score_max = 0
        best_roles = None
        for roles in role_permutations:
            role_score = 0
            important_roles_mmr = []
            for i, player in enumerate(team['players']):
                role_score += getattr(player.roles, roles[i])
                # remember mmrs of carry nad mid players to later check if they are okay
                if roles[i] in ['carry', 'mid']:
                    important_roles_mmr.append(player.ladder_mmr)

            team_ok = all(top2_mmr - x < 1500 for x in important_roles_mmr)
            if role_score > role_score_max and team_ok:
                role_score_max = role_score
                best_roles = roles

        # sort players according to their roles
        sorted_players = []
        role_score = []
        for r in role_names:
            ind = best_roles.index(r)  # index of player for given role
            player = team['players'][ind]
            score = getattr(player.roles, r)

            sorted_players.append(player)
            role_score.append(score)

        team.update({
            'players': sorted_players,
            'role_score': role_score,
            'role_score_sum': role_score_max,
        })

    team_players = 5

    # sort players by mmr
    players.sort(key=lambda x: -x.ladder_mmr)

    # get all possible teams
    teams = list(itertools.combinations(players, team_players))

    # calc avg MMR for each team
    # --------------------------
    # Taking MMR exponent will make difference between high mmr players
    # to have more impact than same difference between low mmr players
    # (good for balancing).
    teams = [
        {
            'players': team,
            'mmr': sum(player.ladder_mmr for player in team) // team_players,
            'mmr_exp': sum(player.ladder_mmr ** mmr_exponent for player in team) // team_players
        }
        for team in teams
    ]

    for team in teams:
        assign_best_roles(team)

    # combine teams into pairs against each other
    half = len(teams) // 2
    answers = zip(teams[:half], list(reversed(teams[half:])))

    # discard answers that place top 2 or lowest 2 players on same team
    top_players = players[:2]
    low_players = players[-2:]
    answers = [
        x for x in answers
            if both_teams_have(x, top_players, 1) and
               both_teams_have(x, low_players, 1)
    ]

    # calc mmr differences for each pair of teams
    answers = [
        {
            'teams': random.sample(answer, len(answer)),  # assign team side randomly (Radiant or Dire)
            'mmr_diff': abs(answer[0]['mmr'] - answer[1]['mmr']),
            'mmr_diff_exp': abs(answer[0]['mmr_exp'] - answer[1]['mmr_exp']),
            'role_score_sum': answer[0]['role_score_sum'] + answer[1]['role_score_sum'],
        }
        for answer in answers
    ]

    # discard answers that have too unbalanced teams
    answers = [x for x in answers if x['mmr_diff'] <= 200]

    # sort answers by mmr difference
    answers.sort(key=lambda x: (-x['role_score_sum'], x['mmr_diff_exp']))

    for answer in answers:
        for team in answer['teams']:
            team['players'] = [(p.name, p.ladder_mmr) for p in team['players']]

    return answers


def balance_from_teams(teams, mmr_exponent=3):
    """
    Takes an already made-up teams and produces balance info for it.
    Used to calculate balance info for custom teams setup

    :param teams: list of teams, where each team is a list of players (name and mmr):
            Example input:

                teams = [
                    [
                        ['Denden',     3000],
                        ['Uvs',        8000],
                        ['Polshy',     5000],
                        ['SMMN',       5500],
                        ['Ulafzs',     6000],
                    ]
                    [
                        ['Lazy Panda', 4000],
                        ['Smile',      5000],
                        ['rawr',       3500],
                        ['Paul',       6200],
                        ['Mikel',      2400],
                    ]
                ]

    :return: dictionary with balance info
    """

    # TODO: make function team_stats cause code repeats one from balance_teams
    teams = [
        {
            'players': team,
            'mmr': sum(player[1] for player in team) // len(team),
            'mmr_exp': sum(player[1] ** mmr_exponent for player in team) // len(team)
        }
        for team in teams
    ]

    # TODO: make function balance_stats cause code repeats one from balance_teams
    answer = {
        'teams': teams,
        'mmr_diff': abs(teams[0]['mmr'] - teams[1]['mmr']),
        'mmr_diff_exp': abs(teams[0]['mmr_exp'] - teams[1]['mmr_exp'])
    }

    return answer


