from django.db import models
from django.conf import settings
import hashlib

class Tenant(models.Model):
    name = models.CharField("用戶名稱",max_length=100, unique=True)
    is_active = models.BooleanField("是否啟用",default=True)
    created_at = models.DateTimeField("建立時間",auto_now_add=True)

    class Meta:
        verbose_name = "用戶"
        verbose_name_plural = "用戶"

    def __str__(self):
        return self.name

class TenantMember(models.Model):
    ROLE_CHOICES = [
        ("owner", "Owner"),           #最高權限
        ("admin", "Admin"),           #後端
        ("operator", "Operator"),     #使用
        ("viewer", "Viewer"),         #觀看
    ]
    tenant = models.ForeignKey(
        Tenant, 
        on_delete=models.CASCADE, 
        related_name="members"
        )
    
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name="tenant_member"
        )
    role = models.CharField("權限",max_length=20, choices=ROLE_CHOICES, default="viewer")
    created_at = models.DateTimeField("加入時間", auto_now_add=True)

    class Meta:
        verbose_name = "租戶成員"
        verbose_name_plural = "租戶成員"
        constraints = [
            models.UniqueConstraint(fields=["tenant", "user"], name="uq_tenant_user_member")
        ]
    def __str__(self):
        return f"{self.tenant} - {self.user} ({self.role})"







class TeaGarden(models.Model):   # 茶園
    name = models.CharField("茶園名稱", max_length=20)
    location_desc = models.CharField("地點", max_length=50, blank=True)
    start_date = models.DateField("飛行日期", null=True, blank=True)
    altitude_m = models.FloatField("海拔高度(公尺)", null=True, blank=True)
    length = models.FloatField("長(公尺)", null=True, blank=True)
    width = models.FloatField("寬(公尺)", null=True, blank=True)
    area_sqm = models.FloatField("面積(平方公尺)", null=True, blank=True)
    note = models.TextField("備註", blank=True)
    tenant = models.ForeignKey(
        Tenant, 
        on_delete=models.CASCADE, 
        related_name="gardens", 
        verbose_name="租戶",
        null=True,
        blank=True
    ) 
    

    class Meta:
        verbose_name = "茶園資訊"
        verbose_name_plural = "茶園資訊"
        constraints = [
            models.UniqueConstraint(fields=["tenant", "name"], name="uq_tenant_garden_name")
        ]      #model.UniqueConstraint的用途在於讓一個用戶綁定一個茶園，但不同茶園可

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # 如果長、寬都有值，就自動計算面積
        if self.length is not None and self.width is not None:
            self.area_sqm = self.length * self.width
        else:
            self.area_sqm = None
        super().save(*args, **kwargs)



class Weather(models.Model):           #天氣
    name = models.CharField("天氣", max_length=100, unique=True)
    description = models.TextField("詳細說明", blank=True)

    class Meta:
        verbose_name = "天氣"
        verbose_name_plural = "天氣"

    def __str__(self):
        return self.name
    


class Flight(models.Model):        #飛行資料
    """一次飛行 / 拍攝任務"""
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE, 
        related_name="flights", 
        verbose_name="租戶",
        null=True,
        blank=True

    )
    
    garden = models.ForeignKey(
        TeaGarden,
        on_delete=models.CASCADE,
        related_name="flights",
        verbose_name="茶園",
    )
    weather = models.ForeignKey(
        Weather,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="flights",
        verbose_name="天氣",
    )
    date = models.DateField("飛行日期")
    times = models.IntegerField("批次", null=True, blank=True)
    start_time = models.TimeField("開始時間", null=True, blank=True)
    pilot_name = models.CharField("飛行員", max_length=20, blank=True)
    class Meta:
        verbose_name = "飛行任務"
        verbose_name_plural = "飛行任務"
        ordering = ["-date", "-start_time"]             #按時間排順序
        indexes = [
            models.Index(fields=["tenant", "date"]),
        ]

    def __str__(self):
        return f"{self.garden.name} - {self.date} - 第{self.times}批" #選單的下拉顯示{name}-2025-11-25s-1


class Photo(models.Model):   
    """
    照片基本資訊：
    - 病蟲害/產量系統共用
    - 偽自動化會把 EXIF 的拍攝時間 & 經緯度塞到這裡
    """
    tenant = models.ForeignKey(
        Tenant, 
        on_delete=models.CASCADE, 
        related_name="photos", 
        verbose_name="租戶",
        null=True,
        blank=True
    )
    garden = models.ForeignKey(
        TeaGarden,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="photos",
        verbose_name="茶園",
    )
    flight = models.ForeignKey(
        Flight,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="photos",
        verbose_name="飛行任務",
    )
    
    image = models.ImageField("照片檔案", upload_to="photos/",null=True,blank=True)

    sha256 = models.CharField("檔案SHA256", max_length=64, db_index=True,null=True, blank=True,unique=True) #避免重複用
    size_bytes = models.BigIntegerField("檔案大小(bytes)", null=True, blank=True) #計算使用空間
    
    taken_at = models.DateTimeField("拍攝時間", null=True, blank=True)
    latitude = models.FloatField("緯度", null=True, blank=True)
    longitude = models.FloatField("經度", null=True, blank=True)
    altitude = models.FloatField("高度(公尺)", null=True, blank=True)
    uploaded_at = models.DateTimeField("上傳時間", auto_now_add=True)
    note = models.TextField("備註", blank=True)

    class Meta:
        verbose_name = "照片"
        verbose_name_plural = "照片"
        ordering = ["-uploaded_at"]

    def __str__(self):
        short_sha = self.sha256[:12] if self.sha256 else "no-sha"
        return f"Photo#{self.id} {short_sha}"

# class InferenceJob(models.Model):
#     STATUS = [
#         ("PENDING", "Pending"),
#         ("RUNNING", "Running"),
#         ("DONE", "Done"),
#         ("FAILED", "Failed"),
#     ]

#     tenant = models.ForeignKey("Tenant", on_delete=models.CASCADE, null=True, blank=True)
#     photo = models.ForeignKey("Photo", on_delete=models.CASCADE, related_name="infer_jobs")

#     status = models.CharField(max_length=16, choices=STATUS, default="PENDING", db_index=True)
#     model_name = models.CharField(max_length=128, default="yolo")   # 例如 best.pt / best_v2.pt
#     model_conf = models.FloatField(default=0.25)

#     result = models.JSONField(null=True, blank=True)        # detections / 統計
#     error_message = models.TextField(blank=True)

#     created_at = models.DateTimeField(auto_now_add=True)
#     started_at = models.DateTimeField(null=True, blank=True)
#     finished_at = models.DateTimeField(null=True, blank=True)

#     class Meta:
#         indexes = [models.Index(fields=["status", "created_at"])]





class YieldRecord(models.Model):           #產量資訊
    """
    產量紀錄
    - 一筆代表某茶園在某日期的「真實產量」
    - 預測值可以之後由模型寫回來
    """
    garden = models.ForeignKey(
        TeaGarden,
        on_delete=models.CASCADE,
        related_name="yield_records",
        verbose_name="茶園",
    )
    date = models.DateField("日期")

    area_sqm = models.FloatField("面積(平方公尺)", null=True, blank=True)

    actual_yield_kg = models.FloatField("實際產量(kg)", null=True, blank=True)
    predicted_yield_kg = models.FloatField("預測產量(kg)", null=True, blank=True)

    latitude = models.FloatField("緯度", null=True, blank=True)   
    longitude = models.FloatField("經度", null=True, blank=True)   
    created_at = models.DateTimeField("建立時間", auto_now_add=True)

    class Meta:
        verbose_name = "產量紀錄"
        verbose_name_plural = "產量紀錄"
        ordering = ["-date"]

    def __str__(self):
        return f"{self.garden.name} - {self.date}"





#紀錄推論結果
class InferenceRun(models.Model):
    photo = models.ForeignKey("Photo", on_delete=models.CASCADE, related_name="inference_runs")
    created_at = models.DateTimeField(auto_now_add=True)

    MODE_CHOICES = [
        ("normal", "Normal YOLO"),
        ("sahi", "SAHI"),
    ]
    mode = models.CharField(max_length=20, choices=MODE_CHOICES,default="normal",)
    bbox_count = models.IntegerField(default=0)
    total_weight = models.FloatField(default=0)
    median_confidence = models.FloatField(default=0)

    area_cm2 = models.FloatField("面積(cm²)", default=0)
    density_g_per_cm2 = models.FloatField("密度(g/cm²)", default=0)
    
    model_name = models.CharField(max_length=200, default="best.pt")  # 或存權重版本
    conf_thres = models.FloatField(default=0.25)
    iou_thres = models.FloatField(default=0.45)

    status = models.CharField(max_length=20, default="done")  # done/failed
    error_message = models.TextField(blank=True, default="")

    elapsed_ms = models.IntegerField(null=True, blank=True)#時間紀錄

    
    overlay_image = models.ImageField(upload_to="overlays/", null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["photo", "mode"],
                name="uq_inference_run_photo_mode",
            )
        ]
        indexes = [
            models.Index(fields=["photo", "mode"], name="idx_run_photo_mode"),
            models.Index(fields=["created_at"], name="idx_run_created_at"),
        ]

    def __str__(self):
        return f"Run#{self.id} photo={self.photo_id} mode={self.mode}"


class Detection(models.Model):
    run = models.ForeignKey(InferenceRun, on_delete=models.CASCADE, related_name="detections")
    cls_id = models.IntegerField()
    cls_name = models.CharField(max_length=100, blank=True, default="")
    conf = models.FloatField()

    x1 = models.FloatField()
    y1 = models.FloatField()
    x2 = models.FloatField()
    y2 = models.FloatField()

    class Meta:
        ordering = ["id"]
        indexes = [
            models.Index(fields=["run"]),
            models.Index(fields=["cls_id"]),
        ]
    def __str__(self):
        return f"Det#{self.id} run={self.run_id} cls={self.cls_name} conf={self.conf:.3f}"