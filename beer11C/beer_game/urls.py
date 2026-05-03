from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView

urlpatterns = [
    path('admin/', admin.site.urls),
    path('favicon.ico', RedirectView.as_view(url='/static/game/favicon.svg', permanent=True)),
    path('i18n/', include('django.conf.urls.i18n')),
    path('', include('game.urls')),
]
