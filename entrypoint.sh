#!/bin/bash

# Fail on any subprocess exting
set -eu

# Run database migrations
python manage.py migrate --noinput

# Start inhouse application
python manage.py runserver 0.0.0.0:8000 &
python manage.py dota_bot -n 3 &
python manage.py discord_bot