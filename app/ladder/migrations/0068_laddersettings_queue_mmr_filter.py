# -*- coding: utf-8 -*-
# Generated by Django 1.9 on 2021-07-12 14:36
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ladder', '0067_auto_20210712_1602'),
    ]

    operations = [
        migrations.AddField(
            model_name='laddersettings',
            name='queue_mmr_filter',
            field=models.PositiveSmallIntegerField(choices=[(0, 'Dota MMR'), (1, 'Ladder MMR')], default=1),
        ),
    ]
