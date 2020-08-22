from collections import Counter
from app.ladder.models import Player
from dal import autocomplete
from django import forms
from django.core.exceptions import ValidationError


class BalancerForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super(BalancerForm, self).__init__(*args, **kwargs)

        for i in range(1, 11):
            self.fields['player_%s' % i] = forms.ModelChoiceField(
                queryset=Player.objects.all(),
                widget=autocomplete.ModelSelect2(url='ladder:player-autocomplete')
            )

    def clean(self):
        cleaned_data = super(BalancerForm, self).clean()

        if self.errors:
            return cleaned_data

        # check for player duplicates
        players = cleaned_data.values()
        counts = Counter(players)
        duplicates = [player for player in counts.keys() if counts[player] > 1]

        if duplicates:
            raise ValidationError(
                'Player duplicates: %(value)s',
                code='duplicates',
                params={'value': ', '.join([p.name for p in duplicates])},
            )

        return cleaned_data


class BalancerCustomForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super(BalancerCustomForm, self).__init__(*args, **kwargs)

        for i in range(1, 11):
            self.fields['player_%s' % i] = forms.CharField(label='Player %s' % i)
            self.fields['MMR_%s' % i] = forms.IntegerField(label='MMR %s' % i, min_value=0, initial=0)

