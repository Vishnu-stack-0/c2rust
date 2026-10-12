from django.db import models


class TranslationJob(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("completed", "Completed"),
        ("verification_failed", "Verification Failed"),
        ("failed", "Failed"),
    ]

    c_code = models.TextField()
    rust_code = models.TextField(blank=True, null=True)
    model_name = models.CharField(max_length=100, default="groq")
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
    )
    error_message = models.TextField(blank=True, null=True)
    verification_report = models.JSONField(blank=True, null=True)
    repair_attempts = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Job {self.id} - {self.status}"


class RepairAttempt(models.Model):
    REASON_CHOICES = [
        ("compile_error", "Compile Error"),
        ("output_mismatch", "Output Mismatch"),
    ]

    job = models.ForeignKey(
        TranslationJob,
        on_delete=models.CASCADE,
        related_name="repairs",
    )
    attempt_number = models.IntegerField()
    reason = models.CharField(max_length=30, choices=REASON_CHOICES)
    old_rust_code = models.TextField()
    new_rust_code = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Repair {self.attempt_number} for Job {self.job.id}"