import os, uuid, hashlib, json, time
from django.http import JsonResponse, HttpResponse
from django.conf import settings
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import render
from django.utils import timezone

from .yolo_detector import infer_image, infer_image_sahi, build_weight_summary
from .models import Photo, InferenceRun, Detection
from statistics import median

FIXED_AREA_CM2 = getattr(settings, "FIXED_AREA_CM2", 4013.0)
def index(request):
    return render(request, "manage/index.html")

def intro_infer(request):
    return render(request, "introduction/infer.html")

def calc_uploaded_file_sha256(uploaded_file):
    hasher = hashlib.sha256()
    for chunk in uploaded_file.chunks():
        hasher.update(chunk)
    return hasher.hexdigest()
#修改class_name
def get_display_cls_name(cls_name):
    label_map = {
        "tea_cut": "cut",
        "tea_cut_green": "green",
    }
    return label_map.get(cls_name, cls_name)


def save_inference_result(photo, infer_result, mode="normal", conf_thres=0.25, iou_thres=0.45):

    detections = infer_result.get("detections", [])
    summary = infer_result.get("summary", {})

    conf_list = [float(det.get("conf", 0.0)) for det in detections]
    bbox_count = len(detections)
    median_confidence = median(conf_list) if conf_list else 0.0

    #直接抓summary計算結果
    total_weight = float(summary.get("total_weight", 0.0))
    area_cm2 = float(infer_result.get("area_cm2", FIXED_AREA_CM2))
    density_g_per_cm2 = float(infer_result.get("density_g_per_cm2", 0.0))

    elapsed_ms = infer_result.get("elapsed_ms")
    overlay_image = infer_result.get("overlay_image")

    

    run, created = InferenceRun.objects.update_or_create(
        photo=photo,
        mode=mode,
        defaults={
            "model_name": "best.pt",
            "conf_thres": conf_thres,
            "iou_thres": iou_thres,
            "status": "done",
            "elapsed_ms": elapsed_ms,
            "overlay_image": overlay_image,
            "bbox_count": bbox_count,
            "total_weight": total_weight,
            "area_cm2": area_cm2,
            "density_g_per_cm2": density_g_per_cm2,
            "median_confidence": median_confidence,
            "error_message": "",
        }
    )

    #避免重新推論同一張圖(修改參數(ex:conf,iou或模型時跑出舊框+新框))
    run.detections.all().delete()

    detection_rows = []
    for det in detections:
        bbox = det.get("bbox", [0, 0, 0, 0])


        #如果是dictionary格式，而不是list的也能取值
        if isinstance(bbox, dict):
            x1 = float(bbox.get("x1", 0.0))
            y1 = float(bbox.get("y1", 0.0))
            x2 = float(bbox.get("x2", 0.0))
            y2 = float(bbox.get("y2", 0.0))
        else:
            x1 = float(bbox[0]) if len(bbox) > 0 else 0.0
            y1 = float(bbox[1]) if len(bbox) > 1 else 0.0
            x2 = float(bbox[2]) if len(bbox) > 2 else 0.0
            y2 = float(bbox[3]) if len(bbox) > 3 else 0.0

        detection_rows.append(
            Detection(
                run=run,
                cls_id=int(det.get("cls_id", 0)),
                cls_name=str(det.get("cls_name", "")),
                conf=float(det.get("conf", 0.0)),
                x1=x1,
                y1=y1,
                x2=x2,
                y2=y2,
            )
        )

    if detection_rows:
        Detection.objects.bulk_create(detection_rows)

    return run

def build_result_from_run(run):

    detections = []
    for i, det in enumerate(run.detections.all().order_by("id")):
        detections.append({
            "id": i,
            "cls_id": det.cls_id,
            "cls_name": det.cls_name,
            "display_cls_name": get_display_cls_name(det.cls_name),  # 顯示名稱
            "conf": det.conf,
            "bbox": [det.x1, det.y1, det.x2, det.y2],
        })
    #套用yolo_detectors的重量分類別計算
    summary = build_weight_summary(detections)
    #回傳偵測和重量
    return {
        "detections": detections,
        "summary": summary,
        "area_cm2": float(run.area_cm2 or 0),
        "density_g_per_cm2": round(float(run.density_g_per_cm2 or 0), 6),
    }


@csrf_exempt
@require_POST
def api_infer(request):
    if "file" not in request.FILES:
        return JsonResponse({"ok": False, "error": "沒上傳圖片"}, status=400)

    f = request.FILES["file"]

    #拿到mode，然後判斷是普通還是sahi，如果沒有通過判斷自動選擇normal
    mode = request.POST.get("mode", "normal").strip().lower()
    if mode not in ("normal", "sahi"):
        mode = "normal"

    #看有沒有收到conf和iou值，沒有的話就給預設值(含例外處理)
    try:
        conf_thres = float(request.POST.get("conf", 0.25))
    except ValueError:
        conf_thres = 0.25

    try:
        iou_thres = float(request.POST.get("iou", 0.45))
    except ValueError:
        iou_thres = 0.45

    # 1. 計算sha256
    sha256_value = calc_uploaded_file_sha256(f)
    #再次改動要先移動回檔案開頭
    f.seek(0)

    # 2. 查是否已有同圖
    existing_photo = Photo.objects.filter(sha256=sha256_value).first()
    photo = None

    #如果有舊圖，回傳inference_run第一筆資料
    if existing_photo:
        old_run = existing_photo.inference_runs.filter(mode=mode).first()

        #如果mode也一樣，直接把舊圖回傳
        if old_run:
            old_result = build_result_from_run(old_run)

            data = {
                "ok": True,
                "duplicate": True,
                "message": f"這是重複圖片，已回傳舊的 {mode} 推論結果",
                "photo_id": existing_photo.id,
                "sha256": sha256_value,
                "saved_as": existing_photo.image.name if existing_photo.image else None,
                "mode": mode,
                "run_id": old_run.id,
                "bbox_count": old_run.bbox_count,
                "total_weight": old_run.total_weight,
                "area_cm2": round(old_run.area_cm2, 2),
                "density_g_per_cm2": round(old_run.density_g_per_cm2, 6),
                "median_confidence": old_run.median_confidence,
                "elapsed_ms": old_run.elapsed_ms,
                "result": old_result,
            }

            text = json.dumps(data, ensure_ascii=False)
            text = text.replace('"result":', '"result":\n')
            return HttpResponse(text, content_type="application/json")
        #如果同圖但是mode不相同，仍然先帶入舊圖才能繼續往下
        photo = existing_photo


    # 3. 存檔到 tmp（不管新圖或同圖不同模式，都要有實體檔給推論用）
    tmp_dir = os.path.join(settings.MEDIA_ROOT, "tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    ext = os.path.splitext(f.name)[1] or ".jpg"
    filename = f"{uuid.uuid4().hex}{ext}"
    save_path = os.path.join(tmp_dir, filename)
    relative_path = f"tmp/{filename}"

    with open(save_path, "wb") as out:
        for chunk in f.chunks():
            out.write(chunk)

    # 4. 只有圖片是新的話才建立 Photo        
    if photo is None:
        photo = Photo.objects.create(
            image=relative_path,
            sha256=sha256_value,
            size_bytes=f.size,
        )

    # 5. 跑推論
    try:
        start_ts = time.perf_counter()

        #選擇模組
        if mode == "sahi":
            result = infer_image_sahi(
            save_path,
            conf=conf_thres,
            iou=iou_thres,
            batch_size=2,
            )
        else:
            result = infer_image(save_path, conf=conf_thres)
        
        #計算推論時間
        elapsed_ms = int((time.perf_counter() - start_ts) * 1000)


    except Exception as e:
        data = {
            "ok": False,
            "error": f"推論失敗: {str(e)}"
        }
        text = json.dumps(data, ensure_ascii=False)
        return HttpResponse(text, content_type="application/json", status=500)

    # 6. 存推論結果
    result_for_save = dict(result)
    result_for_save["elapsed_ms"] = elapsed_ms
    result_for_save["overlay_image"] = result.get("overlay_image", None)
    total_weight = float(result.get("summary", {}).get("total_weight", 0.0))
    area_cm2 = float(FIXED_AREA_CM2)
    density_g_per_cm2 = total_weight / area_cm2 if area_cm2 > 0 else 0.0

    result_for_save["area_cm2"] = area_cm2
    result_for_save["density_g_per_cm2"] = density_g_per_cm2

    response_detections = []
    for det in result_for_save.get("detections", []):
        response_detections.append({
            "id": det.get("id"),
            "cls_id": det.get("cls_id"),
            "cls_name": det.get("cls_name", ""),  # 原始名稱保留
            "display_cls_name": get_display_cls_name(det.get("cls_name", "")),  # 顯示名稱
            "conf": det.get("conf", 0.0),
            "bbox": det.get("bbox", [0, 0, 0, 0]),
    })

    result_for_response = {
        "detections": response_detections,
        "summary": result_for_save.get("summary", {}),
        "elapsed_ms": result_for_save.get("elapsed_ms"),
        "overlay_image": result_for_save.get("overlay_image"),
        "area_cm2": round(result_for_save.get("area_cm2", 0.0), 2),
        "density_g_per_cm2": round(result_for_save.get("density_g_per_cm2", 0.0), 6),
    }

    run = save_inference_result(
            photo=photo,
            infer_result=result_for_save,
            mode=mode,
            conf_thres=conf_thres,
            iou_thres=iou_thres,
        )
    
    data = {
        "ok": True,
        "duplicate": existing_photo is not None,
        "message": f"已完成 {mode} 推論",
        "result": result_for_response,
        "saved_as": photo.image.name if photo.image else None, #錯誤路徑防範
        "photo_id": photo.id,
        "sha256": sha256_value,
        "mode": mode,
        "run_id": run.id,
        "bbox_count": run.bbox_count,
        "total_weight": run.total_weight,
        "area_cm2": round(run.area_cm2, 2),
        "density_g_per_cm2": round(run.density_g_per_cm2, 6),
        "median_confidence": run.median_confidence,
        "elapsed_ms": run.elapsed_ms,
    }

    text = json.dumps(data, ensure_ascii=False)
    text = text.replace('"result":', '"result":\n')

    return HttpResponse(text, content_type="application/json")


def api_infer_status(request):
    return JsonResponse({"ok": True, "status": "ready"})






# history的view
#收到api後跑inferenceRun的圖(order by create_at)
def intro_history(request):
    runs = (
        InferenceRun.objects
        .select_related("photo")
        .order_by("-created_at")
    )

    # 折線圖 (order by create_at)
    runs_for_chart = (
        InferenceRun.objects
        .select_related("photo")
        .order_by("created_at")
    )

    chart_labels = []
    chart_weights = []
    chart_densities = []

    for run in runs_for_chart:
        local_dt = timezone.localtime(run.created_at)
        chart_labels.append(local_dt.strftime("%Y-%m-%d %H:%M:%S"))
        chart_weights.append(float(run.total_weight or 0)) #如果沒有weight就顯示0
        chart_densities.append(float(run.density_g_per_cm2 or 0))

    context = {
        "runs": runs,
        "chart_labels_json": json.dumps(chart_labels, ensure_ascii=False),
        "chart_weights_json": json.dumps([]),#chart_weights代替[]，先不顯示
        "chart_densities_json": json.dumps(chart_densities),
    }

    return render(request, "introduction/history.html", context)