from django import template

from transcription.pipeline import format_timestamp

register = template.Library()


@register.filter
def hms(seconds):
    try:
        return format_timestamp(float(seconds))
    except (TypeError, ValueError):
        return ""


@register.filter
def bn_label(label):
    from transcription.report import bangla_label

    return bangla_label(label or "")
