import calendar

from django.db import models
from multiselectfield import MultiSelectField

from app.balancer.models import BalanceAnswer
from autoslug import AutoSlugField
from app.ladder.managers import PlayerManager, ScoreChangeManager
from solo.models import SingletonModel
from annoying.fields import AutoOneToOneField


def create_roles_pref():
    return RolesPreference.objects.create().id


class RolesPreference(models.Model):
    CHOICES = [(i, i) for i in range(1, 6)]

    carry = models.PositiveSmallIntegerField(choices=CHOICES, default=3)
    mid = models.PositiveSmallIntegerField(choices=CHOICES, default=3)
    offlane = models.PositiveSmallIntegerField(choices=CHOICES, default=3)
    pos4 = models.PositiveSmallIntegerField(choices=CHOICES, default=3)
    pos5 = models.PositiveSmallIntegerField(choices=CHOICES, default=3)


class Player(models.Model):
    name = models.CharField(max_length=200, unique=True)
    dota_mmr = models.PositiveIntegerField()
    dota_id = models.CharField(max_length=200, null=True, blank=True)
    discord_id = models.CharField(max_length=200, null=True, blank=True)
    slug = AutoSlugField(populate_from='name')

    ladder_mmr = models.PositiveIntegerField(default=0)
    score = models.PositiveIntegerField(default=0)
    rank_ladder_mmr = models.PositiveIntegerField(default=0)
    rank_score = models.PositiveIntegerField(default=0)

    voice_issues = models.BooleanField(default=False)
    bot_access = models.BooleanField(default=False)
    vouched = models.BooleanField(default=False)
    blacklist = models.ManyToManyField('self', symmetrical=False, related_name='blacklisted_by')

    # boundaries for ladder mmr
    min_allowed_mmr = models.PositiveIntegerField(default=0)
    max_allowed_mmr = models.PositiveIntegerField(default=0)

    # ban levels
    BAN_PLAYING = 1
    BAN_PLAYING_AND_LOBBY = 2
    BAN_CHOICES = (
        (None, "Not banned"),
        (BAN_PLAYING, 'Banned from playing only'),
        (BAN_PLAYING_AND_LOBBY, 'Banned from playing and lobby'),
    )
    banned = models.PositiveSmallIntegerField(choices=BAN_CHOICES, null=True, blank=True)

    new_reg_pings = models.BooleanField(default=False)
    queue_afk_ping = models.BooleanField(default=True)

    description = models.CharField(max_length=200, null=True, blank=True)
    vouch_info = models.CharField(max_length=200, null=True, blank=True)

    roles = AutoOneToOneField(RolesPreference)

    objects = PlayerManager()

    class Meta:
        ordering = ['rank_ladder_mmr']

    def __str__(self):
        return '%s' % self.name

    @property
    def filter_mmr(self):
        filter = LadderSettings.get_solo().queue_mmr_filter
        return self.dota_mmr if filter == LadderSettings.DOTA_MMR else self.ladder_mmr

    def save(self, *args, **kwargs):
        # TODO: Move this to clean_fields() later
        # TODO: (can't do it atm, because of empty dota_id in test data).
        # TODO: Or even better move this to manager.update_scores()
        # TODO  (this will allow us bulk_update in future)
        self.score = max(self.score or 0, 0)
        self.ladder_mmr = max(self.ladder_mmr or 0, 0)

        created = not self.pk
        if created:
            self.roles = RolesPreference.objects.create()

        super(Player, self).save(*args, **kwargs)

        # give player initial score and mmr
        if created:
            PlayerManager.init_score(self, reset_mmr=True)


class Match(models.Model):
    players = models.ManyToManyField(Player, through='MatchPlayer')
    winner = models.PositiveSmallIntegerField()
    balance = models.OneToOneField(BalanceAnswer, null=True)
    date = models.DateTimeField(auto_now_add=True)
    season = models.PositiveSmallIntegerField(default=1)
    dota_id = models.CharField(max_length=255, null=True)


class MatchPlayer(models.Model):
    match = models.ForeignKey(Match)
    player = models.ForeignKey(Player)
    team = models.PositiveSmallIntegerField()

    class Meta:
        unique_together = ('player', 'match')
        # TODO: replace '-match__date' with '-id' and check that:
        # TODO: - nothing breaks
        # TODO: - speed increased
        ordering = ('-match__date', 'team')


class ScoreChange(models.Model):
    player = models.ForeignKey(Player)
    score_change = models.SmallIntegerField(default=0)
    mmr_change = models.SmallIntegerField(default=0)
    match = models.OneToOneField(MatchPlayer, null=True, blank=True)
    info = models.CharField(max_length=255)
    date = models.DateTimeField(auto_now_add=True)
    season = models.PositiveSmallIntegerField(default=1)

    objects = ScoreChangeManager()

    class Meta:
        unique_together = ('player', 'match')
        ordering = ('-id', )


class LadderSettings(SingletonModel):
    current_season = models.PositiveSmallIntegerField(default=1)
    use_queue = models.BooleanField(default=True)
    mmr_per_game = models.PositiveSmallIntegerField(default=50)
    balance_exponent = models.PositiveSmallIntegerField(default=3)
    afk_allowed_time = models.PositiveSmallIntegerField(default=40)
    afk_response_time = models.PositiveSmallIntegerField(default=5)
    votekick_treshold = models.PositiveSmallIntegerField(default=3)
    pd_votes_needed = models.PositiveSmallIntegerField(default=5)
    dota_lobby_name = models.CharField(max_length=200, default='RD2L')
    noob_queue_suffix = models.CharField(max_length=10, default='LUL', blank=True)
    casual_mode = models.BooleanField(default=False)

    # default draft mode
    AUTO_BALANCE = 0
    PLAYER_DRAFT = 1
    DRAFT_CHOICES = (
        (AUTO_BALANCE, 'Auto balance'),
        (PLAYER_DRAFT, 'Player draft'),
    )
    draft_mode = models.PositiveSmallIntegerField(choices=DRAFT_CHOICES, default=AUTO_BALANCE)

    # queue mmr filter
    DOTA_MMR = 0
    LADDER_MMR = 1
    QFILTER_CHOICES = (
        (DOTA_MMR, 'Dota MMR'),
        (LADDER_MMR, 'Ladder MMR'),
    )
    queue_mmr_filter = models.PositiveSmallIntegerField(choices=QFILTER_CHOICES, default=LADDER_MMR)


class DiscordChannels(SingletonModel):
    polls = models.PositiveIntegerField(null=True, blank=True)
    queues = models.PositiveIntegerField(null=True, blank=True)
    chat = models.PositiveIntegerField(null=True, blank=True)


class DiscordPoll(models.Model):
    name = models.CharField(max_length=200)
    message_id = models.PositiveIntegerField()


class QueueChannel(models.Model):
    name = models.CharField(max_length=200)
    min_mmr = models.PositiveSmallIntegerField(default=0)
    max_mmr = models.PositiveSmallIntegerField(default=0)
    discord_id = models.PositiveIntegerField()
    discord_msg = models.PositiveIntegerField(null=True, blank=True)

    active = models.BooleanField(default=True)
    active_on = MultiSelectField(choices=enumerate(calendar.day_name),
                                 default=range(7),
                                 null=True, blank=True)

    def __str__(self):
        return self.name


class LadderQueue(models.Model):
    players = models.ManyToManyField(Player, through='QueuePlayer')
    active = models.BooleanField(default=True)
    date = models.DateTimeField(auto_now_add=True)
    channel = models.ForeignKey(QueueChannel)
    min_mmr = models.PositiveSmallIntegerField(default=0)
    max_mmr = models.PositiveSmallIntegerField(default=0)
    balance = models.OneToOneField(BalanceAnswer, null=True, blank=True)

    game_start_time = models.DateTimeField(null=True, blank=True)
    game_end_time = models.DateTimeField(null=True, blank=True)
    game_server = models.PositiveIntegerField(null=True, blank=True)

    def __str__(self):
        return f'Queue #{self.id}'

    class Meta:
        ordering = ('-id', )


class QueuePlayer(models.Model):
    queue = models.ForeignKey(LadderQueue)
    player = models.ForeignKey(Player)
    joined_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('player', 'queue')
