from django import template

register = template.Library()


@register.filter
def remaining(value):
    return 100 - value