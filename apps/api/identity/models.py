from django.db import models


class LoginAttemptWindow(models.Model):
    """Expiring abuse budget, not an authentication or business audit record."""

    key = models.CharField(primary_key=True, max_length=64, db_collation="C")
    attempts = models.PositiveIntegerField()
    expires_at = models.DateTimeField(db_index=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.CheckConstraint(condition=models.Q(attempts__gte=1, attempts__lte=30), name="identity_login_attempts_bounded"),
        ]
