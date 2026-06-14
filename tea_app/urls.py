from django.urls import path
from tea_app import manage_views
from tea_app import views   

urlpatterns = [
    #介面url
    path("manage/", manage_views.manage_home, name="manage_home"),
    path("manage/photo/", manage_views.manage_photo_list, name="manage_photo_list"),

    #上傳圖片介面url
    path("introduction/infer/", views.intro_infer, name="intro_infer"),

    #推論api
    path("api/infer/", views.api_infer, name="api_infer"),
    path("api/infer/<int:job_id>/", views.api_infer_status, name="api_infer_status"),
    path("api/infer/status/", views.api_infer_status, name="api_infer_status"),
    #history_api
    path("introduction/history/", views.intro_history, name="intro_history"),

]