FROM python:3.7

WORKDIR /app
RUN apt update && apt install -y git libffi-dev build-essential

ADD . /app
RUN pip install -r requirements.txt
RUN python manage.py collectstatic --noinput
RUN chmod +x entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
