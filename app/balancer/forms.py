from django import forms


class BalancerForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super(BalancerForm, self).__init__(*args, **kwargs)

        for i in xrange(1, 11):
            self.fields['player_%s' % i] = forms.CharField(label='Player %s' % i)
            self.fields['MMR_%s' % i] = forms.IntegerField(label='MMR %s' % i, min_value=0, initial=4000)