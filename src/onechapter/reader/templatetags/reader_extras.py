from django import template

register = template.Library()


@register.filter
def paragraphs(text):
    return [part.strip() for part in (text or "").split("\n\n") if part.strip()]
