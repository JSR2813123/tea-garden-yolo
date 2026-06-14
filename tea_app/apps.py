from django.apps import AppConfig


class TeaappConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tea_app'             #在mySQL裡面用SELECT * tea_app_{class名稱}可以找紀錄的資料
