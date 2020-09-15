from django import template

register = template.Library()


@register.filter
def remaining(value):
    return 100 - value


@register.filter
def index(lst, i):
    return lst[i]
