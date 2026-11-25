from django import template

register = template.Library()


@register.filter
def contact_number(value):
    return '' if not value or value.startswith('TEST-') else value


@register.filter
def catalog_name(value):
    return value.removesuffix('（虚构价格）')
