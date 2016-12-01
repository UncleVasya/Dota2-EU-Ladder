from collections import defaultdict
from django.db import models


class PlayerManager(models.Manager):
    def update_ranks(self):
        # recalculate player rankings based on score
        score_groups = defaultdict(list)
        for player in self.all():
            score_groups[player.score].append(player)

        score_groups = sorted(score_groups.items(), reverse=True)

        for rank, group in enumerate(score_groups):
            for player in group[1]:
                player.rank = rank + 1
                player.save()
