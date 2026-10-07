from django.contrib import admin

from .models import TesterFeedback


@admin.register(TesterFeedback)
class TesterFeedbackAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "days_used", "rating", "short_problems")
    list_filter = ("days_used", "rating")
    readonly_fields = [f.name for f in TesterFeedback._meta.fields]
    ordering = ("-created_at",)

    def short_problems(self, obj):
        return (obj.problems[:60] + "...") if len(obj.problems) > 60 else obj.problems
    short_problems.short_description = "Problems reported"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
