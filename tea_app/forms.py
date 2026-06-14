# tea_app/forms.py
from django import forms
from django.forms.widgets import ClearableFileInput
from .models import Photo


class MultiImageInput(ClearableFileInput):
    """可以一次選多張圖片的 input"""
    allow_multiple_selected = True


class MultiImageField(forms.ImageField):
    """接收「多張」Image 的欄位（回傳 list）"""
    widget = MultiImageInput

    def clean(self, data, initial=None):
        # data 可能是單一檔案或 list
        if not data and initial:
            return initial

        if not data:
            return []

        # 確保是 list
        if not isinstance(data, (list, tuple)):
            data = [data]

        # 對每一張圖片做 ImageField 原本的驗證
        cleaned_files = []
        for f in data:
            cleaned_files.append(super().clean(f, initial))

        return cleaned_files


class PhotoAdminForm(forms.ModelForm):
    #  用自訂的「多張圖片欄位」，名稱叫 images（不是 image）
    images = MultiImageField(label="照片檔案", required=True)

    class Meta:
        model = Photo
        # 注意：這裡不要放 image（model 的 field）
        fields = ["garden", "flight", "note"]

class PhotoManageForm(forms.ModelForm):
    class Meta:
        model = Photo
        fields = ["garden", "flight", "image", "note"] 


