from app.balancer.models import BalanceResult
from django.contrib import admin


class BalanceResultAdmin(admin.ModelAdmin):
    model = BalanceResult

    list_display = ('id',)

admin.site.register(BalanceResult, BalanceResultAdmin)