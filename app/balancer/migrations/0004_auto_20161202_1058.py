# -*- coding: utf-8 -*-
# Generated by Django 1.9 on 2016-12-02 07:58
from __future__ import unicode_literals

from django.db import migrations, models
import jsonfield.fields


class Migration(migrations.Migration):

    dependencies = [
        ('balancer', '0003_auto_20161201_2055'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='balanceanswer',
            name='answer',
        ),
        migrations.AddField(
            model_name='balanceanswer',
            name='mmr_diff',
            field=models.IntegerField(default=1),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='balanceanswer',
            name='mmr_diff_exp',
            field=models.IntegerField(default=1),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='balanceanswer',
            name='teams',
            field=jsonfield.fields.JSONField(default={}),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='balanceresult',
            name='mmr_exponent',
            field=models.FloatField(default=3),
        ),
    ]