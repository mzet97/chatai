from django.contrib import auth
from django.urls import include, path

urlpatterns = [
    path("login/", auth.views.LoginView.as_view(template_name="chat/login.html"), name="login"),
    path("logout/", auth.views.LogoutView.as_view(), name="logout"),
    path("", include("chat.urls")),
]
