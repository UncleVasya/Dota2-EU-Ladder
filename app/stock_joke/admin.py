from django.contrib import admin
from app.stock_joke.models import StockBuyer, StockJokeSettings
from solo.admin import SingletonModelAdmin


class StockBuyerAdmin(admin.ModelAdmin):
    model = StockBuyer

    fieldsets = [
        (None, {'fields': ['name', 'discord_id', 'entry_price']}),
    ]

    list_display = ('name', 'discord_id', 'entry_price')


admin.site.register(StockBuyer, StockBuyerAdmin)
admin.site.register(StockJokeSettings, SingletonModelAdmin)
