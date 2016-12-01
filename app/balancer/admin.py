from app.balancer.models import BalanceResult, BalanceAnswer
from django.contrib import admin


class BalanceAnswerAdmin(admin.ModelAdmin):
    model = BalanceAnswer
    list_display = ('id',)


class BalancerAnswerInline(admin.TabularInline):
    model = BalanceAnswer


class BalanceResultAdmin(admin.ModelAdmin):
    model = BalanceResult
    inlines = (BalancerAnswerInline, )
    list_display = ('id',)


admin.site.register(BalanceAnswer, BalanceAnswerAdmin)
admin.site.register(BalanceResult, BalanceResultAdmin)
