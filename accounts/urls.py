from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("signup/", views.signup, name="signup"),
    path("login/", auth_views.LoginView.as_view(template_name="accounts/login.html", redirect_authenticated_user=True), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("profile/", views.profile, name="profile"),
    path("password/change/", auth_views.PasswordChangeView.as_view(template_name="accounts/form_page.html", extra_context={"title": "Change password"}), name="password_change"),
    path("password/change/done/", auth_views.PasswordChangeDoneView.as_view(template_name="accounts/message.html", extra_context={"title": "Password changed", "body": "Your password has been updated."}), name="password_change_done"),
    path("password/reset/", auth_views.PasswordResetView.as_view(template_name="accounts/form_page.html", extra_context={"title": "Reset password"}), name="password_reset"),
    path("password/reset/sent/", auth_views.PasswordResetDoneView.as_view(template_name="accounts/message.html", extra_context={"title": "Check your email", "body": "If an account exists for that email, we sent a reset link."}), name="password_reset_done"),
    path("password/reset/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(template_name="accounts/form_page.html", extra_context={"title": "Choose a new password"}), name="password_reset_confirm"),
    path("password/reset/complete/", auth_views.PasswordResetCompleteView.as_view(template_name="accounts/message.html", extra_context={"title": "Password reset", "body": "You can now log in with your new password."}), name="password_reset_complete"),
]
