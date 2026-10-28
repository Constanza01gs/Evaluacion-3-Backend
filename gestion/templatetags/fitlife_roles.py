from django import template

register = template.Library()


@register.filter
def es_administrador(user):
    return bool(user.is_authenticated and (user.is_superuser or user.groups.filter(name='Administrador').exists()))
