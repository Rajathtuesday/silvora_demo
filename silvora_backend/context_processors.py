from django.conf import settings


def site_links(request):
    """Links every public page shares, so a change is made in one place.

    play_store_url is where every download button points. It moves from the
    closed-testing page to the store listing once the app is public.
    """
    return {"play_store_url": settings.PLAY_STORE_URL}
