from django import forms
from django.contrib.auth.forms import UserCreationForm

from .models import User


class SignupForm(UserCreationForm):
    full_name = forms.CharField(max_length=150, label="Full name")
    organization = forms.CharField(max_length=200, required=False, label="Organization / university")
    phone = forms.CharField(max_length=30, required=False, label="Phone / WhatsApp")
    accept_terms = forms.BooleanField(
        label="I agree to the Terms of Service and Privacy Policy", required=True
    )

    class Meta:
        model = User
        fields = ("full_name", "email", "organization", "phone")

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("full_name", "organization", "phone")
