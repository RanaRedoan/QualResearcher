from pathlib import Path

from django import forms
from django.conf import settings

from .models import Job


class UploadForm(forms.Form):
    audio = forms.FileField(label="Audio or video file",
                            widget=forms.ClearableFileInput(attrs={"accept": "audio/*,video/*"}))
    title = forms.CharField(max_length=200, label="Title", help_text="e.g. IDI-07 Female garment worker, Gazipur")
    interview_type = forms.ChoiceField(choices=Job.InterviewType.choices, initial=Job.InterviewType.IDI)
    expected_speakers = forms.IntegerField(min_value=1, max_value=20, required=False, label="Number of speakers")
    context_notes = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 3}), max_length=2000,
        label="Context for the AI",
        help_text="Study topic, local place names, NGO or programme names, technical words.",
    )
    translate = forms.BooleanField(
        required=False, initial=False, label="Also translate to English",
        help_text="Off = Bangla transcript only: about half the time and half the AI usage. "
                  "You can still add the English translation later from the transcript page.",
    )
    retention_days = forms.TypedChoiceField(
        coerce=int, label="Delete the audio after",
        choices=[(d, f"{d} day" + ("s" if d > 1 else "")) for d in settings.RETENTION_CHOICES_DAYS],
        initial=1,
    )
    # respondent profile — printed at the top of the Word report
    respondent_id = forms.CharField(max_length=60, required=False, label="Respondent ID")
    age = forms.CharField(max_length=20, required=False)
    gender = forms.CharField(max_length=30, required=False)
    location = forms.CharField(max_length=120, required=False)
    occupation = forms.CharField(max_length=120, required=False)
    education = forms.CharField(max_length=120, required=False)
    interview_date = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    interviewer = forms.CharField(max_length=120, required=False)
    other = forms.CharField(max_length=300, required=False, label="Other notes")

    consent = forms.BooleanField(
        required=True,
        label="I confirm that participants gave informed consent to be recorded, and that I am uploading "
              "this under my study's ethics/IRB approval and any community agreements.",
    )

    # desktop app only: which saved Gemini key to use (filled in __init__)
    gemini_key = forms.ChoiceField(required=False, label="Gemini key", choices=())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if settings.DESKTOP:
            from desktop import store

            cfg = store.load()
            self.fields["gemini_key"].choices = [(k["id"], k["name"]) for k in cfg["keys"]]
            self.fields["gemini_key"].initial = cfg["default_key"]
        else:
            del self.fields["gemini_key"]

    DEMOGRAPHIC_KEYS = ("respondent_id", "age", "gender", "location", "occupation", "education",
                        "interview_date", "interviewer", "other")

    def clean_audio(self):
        f = self.cleaned_data["audio"]
        ext = Path(f.name).suffix.lower()
        if ext not in settings.ALLOWED_AUDIO_EXTENSIONS:
            raise forms.ValidationError(
                "Unsupported file type. Use: " + ", ".join(e.strip(".").upper() for e in settings.ALLOWED_AUDIO_EXTENSIONS)
            )
        if f.size > settings.MAX_UPLOAD_MB * 1024 * 1024:
            raise forms.ValidationError(f"File is larger than {settings.MAX_UPLOAD_MB} MB.")
        return f

    def demographics(self):
        data = {}
        for key in self.DEMOGRAPHIC_KEYS:
            value = self.cleaned_data.get(key)
            if value:
                data[key] = value.strftime("%d %b %Y") if hasattr(value, "strftime") else str(value).strip()
        return data
