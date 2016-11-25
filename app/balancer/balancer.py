import itertools


def balance_teams(players):
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

    team_players = 5

    # sort players by mmr
    players.sort(key=lambda x: -x[1])

    # get all possible teams
    teams = list(itertools.combinations(players, team_players))

    # calc avg MMR for each team
    teams = [
        {
            'players': team,
            'mmr': sum(player[1] for player in team) / team_players
        }
        for team in teams
    ]

    # combine teams into pairs against each other
    half = len(teams) / 2
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
            'teams': answer,
            'mmr_diff': abs(answer[0]['mmr'] - answer[1]['mmr'])
        }
        for answer in answers
    ]

    # sort answers by mmr difference
    answers.sort(key=lambda x: x['mmr_diff'])

    return answers
