from django import template

register = template.Library()


@register.filter
def dictget(dictionary, key):
    """
    Allows dictionary access by dynamic key in templates.

    Usage:
        {{ my_dict|dictget:key }}
    """
    if dictionary is None:
        return None

    try:
        return dictionary.get(key)
    except AttributeError:
        try:
            return dictionary[key]
        except Exception:
            return None
