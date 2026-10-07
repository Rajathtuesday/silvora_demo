from django.shortcuts import render

from .models import TesterFeedback

_VALID_DAYS = {choice.value for choice in TesterFeedback.DaysUsed}


def beta_feedback(request):
    if request.method == "POST":
        days_used = request.POST.get("days_used", "")
        try:
            rating = int(request.POST.get("rating", ""))
        except ValueError:
            rating = None

        errors = []
        if days_used not in _VALID_DAYS:
            errors.append("Please choose how many days you used the app.")
        if rating is None or not (1 <= rating <= 5):
            errors.append("Please choose a rating from 1 to 5.")

        if not errors:
            TesterFeedback.objects.create(
                days_used=days_used,
                used_uploading=bool(request.POST.get("used_uploading")),
                used_downloading=bool(request.POST.get("used_downloading")),
                used_recovery_phrase=bool(request.POST.get("used_recovery_phrase")),
                used_just_looking=bool(request.POST.get("used_just_looking")),
                used_other=request.POST.get("used_other", "").strip()[:200],
                problems=request.POST.get("problems", "").strip(),
                confusing=request.POST.get("confusing", "").strip(),
                would_keep_using=request.POST.get("would_keep_using", "").strip(),
                rating=rating,
                other_comments=request.POST.get("other_comments", "").strip(),
            )
            return render(request, "feedback/thanks.html")

        return render(request, "feedback/form.html", {"errors": errors, "posted": request.POST})

    return render(request, "feedback/form.html")
