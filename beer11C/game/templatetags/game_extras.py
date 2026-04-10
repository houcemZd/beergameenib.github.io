from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    """Allow dict lookups with a variable key in templates: {{ dict|get_item:key }}"""
    if not dictionary:
        return None
    return dictionary.get(key)
