from pathlib import Path

from django.conf import settings


def _asset_version():
    """Changes whenever site.css changes, so browsers never keep showing an old design."""
    try:
        return str(int((Path(settings.BASE_DIR) / "static" / "css" / "site.css").stat().st_mtime))
    except OSError:
        return "1"


def site(request):
    return {
        "SITE_NAME": settings.SITE_NAME,
        "SITE_TAGLINE": settings.SITE_TAGLINE,
        "SUPPORT_WHATSAPP": settings.SUPPORT_WHATSAPP,
        "BKASH_NUMBER": settings.BKASH_NUMBER,
        "BKASH_ACCOUNT_TYPE": settings.BKASH_ACCOUNT_TYPE,
        "RETENTION_MAX_DAYS": max(settings.RETENTION_CHOICES_DAYS),
        "TRIAL_MINUTES": settings.TRIAL_MINUTES,
        "BILLING_ENABLED": settings.BILLING_ENABLED,
        "LOCAL_MODE": settings.LOCAL_MODE,
        "ASSET_VERSION": _asset_version(),
        "DEVELOPER_NAME": settings.DEVELOPER_NAME,
        "DEVELOPER_URL": settings.DEVELOPER_URL,
        "DEVELOPER_EMAIL": settings.DEVELOPER_EMAIL,
    }
