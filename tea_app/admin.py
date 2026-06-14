from django.contrib import admin, messages  
from django.db import IntegrityError
import hashlib
from .models import (
    Tenant,
    TenantMember,
    TeaGarden,           #茶園資訊
    Flight,              #飛行資訊
    Photo,               #照片
    Weather,             #天氣
    YieldRecord,         #推論結果
    
)

from .forms import PhotoAdminForm

try:
    from .yolo_detector import run_detection
except Exception:
    run_detection = None

# from .yolo_detector import run_detection   #YOLO用

class TenantScopedAdmin(admin.ModelAdmin):
    """
    非 superuser：
    只看得到自己 tenant 的資料
    新增/編輯時自動綁 tenant
    而不是像原本把用admin.ModelAdmin會讀全庫的資料，這樣就沒有做到權限分離
    改成 TenantScopedAdmin就可以 
    """

    tenant_field_name = "tenant"  # 預設欄位名是 tenant

    def _get_current_tenant(self, request):
        if request.user.is_superuser:
            return None
        membership = request.user.tenant_memberships.select_related("tenant").first()
        return membership.tenant if membership else None

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        tenant = self._get_current_tenant(request)
        if not tenant:
            return qs.none()
        return qs.filter(**{self.tenant_field_name: tenant})

    def save_model(self, request, obj, form, change):
        if not request.user.is_superuser:
            tenant = self._get_current_tenant(request)
            if tenant:
                setattr(obj, self.tenant_field_name, tenant)
        super().save_model(request, obj, form, change)

    def get_form(self, request, obj=None, **kwargs):
        """
        在 admin 表單隱藏 tenant 欄位（避免手動亂選）
        """
        form = super().get_form(request, obj, **kwargs)
        if not request.user.is_superuser and self.tenant_field_name in form.base_fields:
            form.base_fields[self.tenant_field_name].disabled = True
        return form


@admin.register(TeaGarden)
class TeaGardenAdmin(TenantScopedAdmin):
    list_display = ("name", "tenant", "location_desc", "start_date","altitude_m", "length", "width", "area_sqm")
    search_fields = ("name", "location_desc")
    readonly_fields = ("area_sqm",)


@admin.register(Weather)
class WeatherAdmin(admin.ModelAdmin):
    list_display = ("name","description")
    search_fields = ("name","description")

@admin.register(Flight)
class FlightAdmin(TenantScopedAdmin):
    list_display = ("garden","tenant", "weather", "date", "times")  #橫向的欄位名稱
    list_filter = ("tenant", "garden", "weather", "date", "pilot_name")          #右側的篩選單
    search_fields = ("garden__name", "pilot_name")


@admin.register(Photo)
class PhotoAdmin(TenantScopedAdmin):
    form = PhotoAdminForm   
    list_display = (
        "id",
        "tenant",
        "garden",
        "flight",
        "taken_at",
        "latitude",
        "longitude",
        "altitude",
        "uploaded_at",
        "image",
        "note"
    )
    list_filter = ("tenant", "garden", "flight")
    search_fields = ("garden__name", "flight__description", "sha256")
    readonly_fields = ("uploaded_at", "sha256", "size_bytes")

    actions = ["run_yolo_on_photos"] 


    def run_yolo_on_photos(self, request, queryset):

        if run_detection is None:
            self.message_user(request, "YOLO 模組未載入（yolo_detector 匯入失敗）", level=messages.ERROR)
            return
        

        """
        Admin action：
        勾選幾張 Photo 後，從 action 下拉選「執行 YOLO 偵測」，
        就會針對每一張照片跑 YOLO，並建立 PestDetection 記錄。
        """
        
        YOLO_CLASS_NAME = {
            0: "tea_cut",
            1: "tea_cut_bad_spread",
            2: "tea_cut_bad_v"
        }

        created_count = 0

        for photo in queryset:
            if not photo.image:
                continue  # 沒檔案就跳過
            image_path = photo.image.path  # 照片實際檔案路徑
            detections = run_detection(image_path)
            

            for det in detections:
                cls_id = det["class_id"]
                conf = det["confidence"]

                # 找到對應的 PestCategory（用名稱對應，較直覺）
                class_name = YOLO_CLASS_NAME.get(cls_id)
                if not class_name:
                    continue  # YOLO 有偵測到但我們沒有對應的類別，就跳過

                created_count += 1

        self.message_user(
            request,
            f"已為 {queryset.count()} 張照片建立 {created_count} 筆偵測結果。",
            level=messages.INFO,
        )

    run_yolo_on_photos.short_description = "執行 YOLO 偵測(best.pt)"  # admin action 顯示文字



    def save_model(self, request, obj, form, change):

        if change:
            # 當hash為Null 
            # 則編輯單筆：補齊 tenant 
            if not request.user.is_superuser:
                tenant = self._get_current_tenant(request)
                if tenant:
                    obj.tenant = tenant

            if obj.image and not obj.sha256:
                obj.sha256 = self._calc_sha256(obj.image)
                try:
                    obj.size_bytes = obj.image.size
                except Exception:
                    pass

            super().save_model(request, obj, form, change)
            return


        # ➜ 新增情境（Add），從表單拿出多張圖片
        images = form.cleaned_data.get("images") or []
        garden = form.cleaned_data.get("garden")
        flight = form.cleaned_data.get("flight")
        note = form.cleaned_data.get("note")

        tenant = None if request.user.is_superuser else self._get_current_tenant(request)

        for img in images:
            sha256 = self._calc_sha256(img)
            size_bytes = getattr(img, "size", None)

            # 直接create；
            # 若加了UNIQUE(tenant, sha256)，
            # 同租戶重複圖會產生IntegrityError
            try:
                Photo.objects.create(
                    tenant=tenant if tenant else getattr(obj, "tenant", None),
                    garden=garden,
                    flight=flight,
                    note=note,
                    image=img,
                    sha256=sha256,
                    size_bytes=size_bytes,
                )
            except IntegrityError:
                self.message_user(request, f"重複圖片已跳過(sha256={sha256[:12]}...)", level=messages.WARNING)


    def _calc_sha256(self, django_file):
        """
        用上傳檔案算 sha256，這樣的話可以避免記憶體過載
        """
        h = hashlib.sha256()
        if hasattr(django_file, "chunks"):
            for chunk in django_file.chunks():
                h.update(chunk)
            return h.hexdigest()
        
        with open(django_file.path, "rb") as fp:
            for chunk in iter(lambda: fp.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()


@admin.register(YieldRecord)
class YieldRecordAdmin(admin.ModelAdmin):
    list_display = (
        "garden",
        "date",
        "area_sqm",
        "actual_yield_kg",
        "predicted_yield_kg",
        "latitude",
        "longitude",
    )
    list_filter = ("garden", "date")
    search_fields = ("garden__name", "note")
    readonly_fields = ("created_at",)