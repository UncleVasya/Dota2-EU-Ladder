from django import forms
from django_reverse_admin import ReverseModelAdmin

from app.ladder.models import Player, Match, MatchPlayer, ScoreChange, LadderSettings, LadderQueue, QueuePlayer, \
    QueueChannel, RolesPreference, DiscordChannels, DiscordPoll
from django.contrib import admin
from django.db.models import Prefetch
from dal import autocomplete
from solo.admin import SingletonModelAdmin


class BlacklistInlineForm(forms.ModelForm):
    from_player = forms.ModelChoiceField(
        queryset=Player.objects.all(),
        widget=autocomplete.ModelSelect2(url='ladder:player-autocomplete')
    )
    to_player = forms.ModelChoiceField(
        queryset=Player.objects.all(),
        widget=autocomplete.ModelSelect2(url='ladder:player-autocomplete')
    )

    class Meta:
        model = Player.blacklist.through
        fields = '__all__'


class BlacklistInline(admin.TabularInline):
    model = Player.blacklist.through
    form = BlacklistInlineForm
    fk_name = 'from_player'


class BlacklistedByInline(admin.TabularInline):
    model = Player.blacklist.through
    form = BlacklistInlineForm
    fk_name = 'to_player'


class PlayerAdmin(ReverseModelAdmin):
    model = Player

    fieldsets = [
        (None, {'fields': ['name', 'dota_mmr', 'dota_id', 'discord_id', 'voice_issues', 'bot_access', 'vouched', 'banned']}),
        (None, {'fields': ['ladder_mmr', 'score']}),
        (None, {'fields': ['description', 'vouch_info']}),
        (None, {'fields': ['rank_ladder_mmr', 'rank_score']}),
        # (None, {'fields': ['min_allowed_mmr', 'max_allowed_mmr']}),
        (None, {'fields': ['new_reg_pings']}),
        (None, {'fields': ['queue_afk_ping']}),
    ]
    readonly_fields = ('ladder_mmr', 'score', 'rank_ladder_mmr', 'rank_score')

    list_display = ('name', 'rank_ladder_mmr', 'score', 'dota_mmr', 'dotabuff_link', 'discord_id', 'vouched')
    search_fields = ('=name',)

    inline_type = 'tabular'
    inline_reverse = [
        ('roles', {'fields': ['carry', 'mid', 'offlane', 'pos4', 'pos5']})
    ]
    # inlines = (BlacklistInline, BlacklistedByInline)

    def dotabuff_link(self, obj):
        dotabuff = f'https://www.dotabuff.com/players/{obj.dota_id}'
        return f'<a href="{dotabuff}">{obj.dota_id}</a>'

    dotabuff_link.allow_tags = True

    def save_model(self, request, obj, form, change):
        super(PlayerAdmin, self).save_model(request, obj, form, change)

        Player.objects.update_ranks()


class MatchPlayerInlineForm(forms.ModelForm):
    player = forms.ModelChoiceField(
        queryset=Player.objects.all(),
        widget=autocomplete.ModelSelect2(url='ladder:player-autocomplete')
    )

    class Meta:
        model = Player
        fields = '__all__'


class MatchPlayerInline(admin.TabularInline):
    model = MatchPlayer
    form = MatchPlayerInlineForm
    min_num = 10
    max_num = 10


class MatchAdmin(admin.ModelAdmin):
    model = Match

    fieldsets = [
        (None, {'fields': ['date', 'winner', 'season', 'dota_id']}),
    ]
    readonly_fields = ['date']

    inlines = (MatchPlayerInline, )

    list_display = ('date', 'dota_id')


class ScoreChangeAdminForm(forms.ModelForm):
    player = forms.ModelChoiceField(
        queryset=Player.objects.all(),
        widget=autocomplete.ModelSelect2(url='ladder:player-autocomplete')
    )


class ScoreChangeAdmin(admin.ModelAdmin):
    model = ScoreChange
    form = ScoreChangeAdminForm

    fieldsets = [
        (None, {'fields': ['player', 'mmr_change', 'score_change', 'info', 'season']}),
    ]

    list_display = ('date', 'player', 'mmr_change', 'score_change', 'info')

    def save_model(self, *args, **kwargs):
        super(ScoreChangeAdmin, self).save_model(*args, **kwargs)

        Player.objects.update_ranks()


class QueuePlayerInlineForm(forms.ModelForm):
    player = forms.ModelChoiceField(
        queryset=Player.objects.all(),
        widget=autocomplete.ModelSelect2(url='ladder:player-autocomplete')
    )

    class Meta:
        model = Player
        fields = '__all__'


class QueuePlayerInline(admin.TabularInline):
    model = QueuePlayer
    form = QueuePlayerInlineForm
    max_num = 10


class LadderQueueAdmin(admin.ModelAdmin):
    model = LadderQueue

    fieldsets = [
        (None, {'fields': ['date', 'active', 'min_mmr', 'channel']}),
        (None, {'fields': ['game_start_time', 'game_end_time']}),
    ]
    readonly_fields = ['date']

    inlines = (QueuePlayerInline, )

    list_display = ('date', 'active', 'min_mmr', 'channel')


class QueueChannelAdmin(admin.ModelAdmin):
    model = QueueChannel

    fieldsets = [
        (None, {'fields': ['name', 'min_mmr', 'discord_id', 'discord_msg']}),
    ]

    list_display = ('name', 'min_mmr', 'discord_id')


class DiscordPollAdmin(admin.ModelAdmin):
    model = DiscordPoll

    fieldsets = [
        (None, {'fields': ['name', 'message_id']}),
    ]

    list_display = ('name', 'message_id')


admin.site.register(Player, PlayerAdmin)
admin.site.register(ScoreChange, ScoreChangeAdmin)
admin.site.register(Match, MatchAdmin)

admin.site.register(LadderSettings, SingletonModelAdmin)

admin.site.register(LadderQueue, LadderQueueAdmin)
admin.site.register(QueueChannel, QueueChannelAdmin)

admin.site.register(DiscordChannels, SingletonModelAdmin)
admin.site.register(DiscordPoll, DiscordPollAdmin)

# admin.site.register(Player.blacklist.through)
