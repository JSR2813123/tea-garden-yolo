# teaplatform/urls.py
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from tea_app import views  

urlpatterns = [
    path("admin/", admin.site.urls),
    path("",include("tea_app.urls")),

    path('home', views.index, name='home'),  # 首頁路由
    path('accounts/', include('django.contrib.auth.urls')),  # ⭐ 登入/登出


]

if settings.DEBUG:           #讓開發模式讀取上傳檔案的
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT) 
