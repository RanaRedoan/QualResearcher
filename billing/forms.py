import re

from django import forms

from .models import PaymentRequest, Plan


class PaymentForm(forms.ModelForm):
    plan = forms.ModelChoiceField(queryset=Plan.objects.filter(is_active=True), empty_label=None)

    class Meta:
        model = PaymentRequest
        fields = ("plan", "sender_number", "trx_id")
        labels = {
            "sender_number": "bKash number you paid from",
            "trx_id": "bKash Transaction ID (TrxID)",
        }

    def clean_sender_number(self):
        number = re.sub(r"[^\d+]", "", self.cleaned_data["sender_number"])
        if not re.fullmatch(r"(\+?88)?01\d{9}", number):
            raise forms.ValidationError("Enter a valid Bangladeshi mobile number, e.g. 01XXXXXXXXX.")
        return number[-11:]

    def clean_trx_id(self):
        trx = self.cleaned_data["trx_id"].strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{8,12}", trx):
            raise forms.ValidationError("A bKash TrxID is 8–12 letters/numbers, e.g. 9AB1CD2EFG.")
        if PaymentRequest.objects.filter(trx_id=trx).exists():
            raise forms.ValidationError("This Transaction ID has already been submitted.")
        return trx
