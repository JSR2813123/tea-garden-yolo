from pathlib import Path
from ultralytics import YOLO
import cv2
import time

# 一開始先設定 yolo 的 best.pt 路徑
_BASE_DIR = Path(__file__).resolve().parent.parent
_WEIGHT_PATH = _BASE_DIR / "weights" / "best.pt"

_model = None

CLASS_WEIGHT = {
    "tea_cut": 1.31,
    "tea_cut_green": 0.65,
}


def get_model():
    global _model
    if _model is None:
        if not _WEIGHT_PATH.exists():
            raise FileNotFoundError(f"YOLO weights not found: {_WEIGHT_PATH}")
        _model = YOLO(str(_WEIGHT_PATH))
    return _model


def build_weight_summary(dets):
    class_counts = {}
    class_weights = {}
    total_weight = 0.0

    for det in dets:
        cls_name = det.get("cls_name", "unknown")
        class_counts[cls_name] = class_counts.get(cls_name, 0) + 1

        unit_weight = CLASS_WEIGHT.get(cls_name, 0.0)
        class_weights[cls_name] = class_weights.get(cls_name, 0.0) + unit_weight
        total_weight += unit_weight

    class_weights = {k: round(v, 2) for k, v in class_weights.items()}
    total_weight = round(total_weight, 2)

    return {
        "class_counts": class_counts,
        "class_weights": class_weights,
        "total_weight": total_weight,
    }


def infer_image(image_path: str, conf: float = 0.25):
    model = get_model()

    results = model.predict(
        source=image_path,
        conf=conf,
        device="cpu",
        verbose=False
    )

    r0 = results[0]
    names = r0.names

    dets = []
    if r0.boxes is None or len(r0.boxes) == 0:
        return {
            "detections": [],
            "summary": build_weight_summary([]),
        }

    xyxy = r0.boxes.xyxy.cpu().tolist()
    confs = r0.boxes.conf.cpu().tolist()
    clss = r0.boxes.cls.cpu().tolist()

    for i, (box, cf, cid) in enumerate(zip(xyxy, confs, clss)):
        cid_int = int(cid)
        dets.append({
            "id": i,
            "bbox": [float(box[0]), float(box[1]), float(box[2]), float(box[3])],
            "cls_id": cid_int,
            "cls_name": names.get(cid_int, str(cid_int)),
            "conf": float(cf),
        })

    summary = build_weight_summary(dets)

    return {
        "detections": dets,
        "summary": summary,
    }


def chunk_list(items, n):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def generate_starts(full_size: int, slice_size: int, overlap_ratio: float):
    """
    產生每個 patch 的起始座標，確保最後一塊會貼齊邊界。
    """
    if full_size <= slice_size:
        return [0]

    stride = max(1, int(slice_size * (1 - overlap_ratio)))
    starts = []
    pos = 0

    while True:
        starts.append(pos)

        if pos + slice_size >= full_size:
            break

        next_pos = pos + stride
        if next_pos + slice_size >= full_size:
            next_pos = full_size - slice_size

        if next_pos == pos:
            break

        pos = next_pos

    # 去重，保險
    dedup = []
    seen = set()
    for s in starts:
        if s not in seen:
            dedup.append(s)
            seen.add(s)
    return dedup


def build_patch_records(image, slice_height=640, slice_width=640,
                        overlap_height_ratio=0.2, overlap_width_ratio=0.2):
    """
    手動切 patch，並保留每塊 patch 對應原圖的位置。
    邊界不足 640x640 的 patch 會補黑邊。
    """
    h, w = image.shape[:2]

    y_starts = generate_starts(h, slice_height, overlap_height_ratio)
    x_starts = generate_starts(w, slice_width, overlap_width_ratio)

    patch_records = []

    for y in y_starts:
        for x in x_starts:
            crop = image[y:min(y + slice_height, h), x:min(x + slice_width, w)].copy()
            orig_h, orig_w = crop.shape[:2]

            # 邊界 patch 補成固定大小，batch 比較穩
            if orig_h != slice_height or orig_w != slice_width:
                padded = cv2.copyMakeBorder(
                    crop,
                    top=0,
                    bottom=slice_height - orig_h,
                    left=0,
                    right=slice_width - orig_w,
                    borderType=cv2.BORDER_CONSTANT,
                    value=(0, 0, 0)
                )
            else:
                padded = crop

            patch_records.append({
                "patch_img": padded,
                "offset_x": x,
                "offset_y": y,
                "orig_w": orig_w,
                "orig_h": orig_h,
            })

    return patch_records


def box_iou(box1, box2):
    """
    box format: [x1, y1, x2, y2]
    """
    x1 = max(box1[0], box2[0])
    y1 = max(box1[1], box2[1])
    x2 = min(box1[2], box2[2])
    y2 = min(box1[3], box2[3])

    inter_w = max(0.0, x2 - x1)
    inter_h = max(0.0, y2 - y1)
    inter_area = inter_w * inter_h

    area1 = max(0.0, box1[2] - box1[0]) * max(0.0, box1[3] - box1[1])
    area2 = max(0.0, box2[2] - box2[0]) * max(0.0, box2[3] - box2[1])

    union = area1 + area2 - inter_area
    if union <= 0:
        return 0.0

    return inter_area / union


def nms_detections(detections, iou_thres=0.2, center_dist_thres=18.0,):
    #同類別先比iou，然後看有沒有通過中心距離測試，
    if not detections:
        return []

    grouped = {}
    for det in detections:
        key = det["cls_id"]
        grouped.setdefault(key, []).append(det)

    kept = []

    for _, dets in grouped.items():
        dets = sorted(dets, key=lambda d: d["conf"], reverse=True)

        while dets:
            best = dets.pop(0)
            kept.append(best)

            remain = []
            best_box = best["bbox"]

            for det in dets:
                other_box = det["bbox"]

                iou = box_iou(best_box, other_box)
                def box_center(box):
                    cx = (box[0] + box[2]) / 2.0
                    cy = (box[1] + box[3]) / 2.0
                    return cx, cy


                def center_distance(box1, box2):
                    cx1, cy1 = box_center(box1)
                    cx2, cy2 = box_center(box2)
                    dx = cx1 - cx2
                    dy = cy1 - cy2
                    return (dx * dx + dy * dy) ** 0.5
                dist = center_distance(best_box, other_box)

                # 只要 IoU 太高，且中心距離太近，就視為重複
                is_duplicate = (iou >= iou_thres) and (dist <= center_dist_thres)

                if not is_duplicate:
                    remain.append(det)
            dets = remain
            

    # 回傳前重新編 id
    kept = sorted(kept, key=lambda d: d["conf"], reverse=True)
    for i, det in enumerate(kept):
        det["id"] = i

    return kept


def predict_patch_batches(model, patch_records, conf=0.25, batch_size=3):
    """
    把 patch_records 每 batch_size 張一起丟進 model.predict()
    """
    all_dets = []

    for group in chunk_list(patch_records, batch_size):
        patch_imgs = [item["patch_img"] for item in group]

        results = model.predict(
            source=patch_imgs,
            conf=conf,
            device="cpu",
            verbose=False
        )

        for item, r in zip(group, results):
            names = r.names

            if r.boxes is None or len(r.boxes) == 0:
                continue

            xyxy = r.boxes.xyxy.cpu().tolist()
            confs = r.boxes.conf.cpu().tolist()
            clss = r.boxes.cls.cpu().tolist()

            offset_x = item["offset_x"]
            offset_y = item["offset_y"]
            orig_w = item["orig_w"]
            orig_h = item["orig_h"]

            for box, cf, cid in zip(xyxy, confs, clss):
                local_x1, local_y1, local_x2, local_y2 = map(float, box)

                # 過濾掉落在 padding 黑邊區域的框
                if local_x1 >= orig_w or local_y1 >= orig_h:
                    continue

                local_x1 = max(0.0, min(local_x1, float(orig_w)))
                local_y1 = max(0.0, min(local_y1, float(orig_h)))
                local_x2 = max(0.0, min(local_x2, float(orig_w)))
                local_y2 = max(0.0, min(local_y2, float(orig_h)))

                if local_x2 <= local_x1 or local_y2 <= local_y1:
                    continue

                cid_int = int(cid)

                all_dets.append({
                    "id": -1,
                    "bbox": [
                        local_x1 + offset_x,
                        local_y1 + offset_y,
                        local_x2 + offset_x,
                        local_y2 + offset_y,
                    ],
                    "cls_id": cid_int,
                    "cls_name": names.get(cid_int, str(cid_int)),
                    "conf": float(cf),
                })

    return all_dets


def infer_image_sahi(
    image_path: str,
    conf: float = 0.25,
    iou: float = 0.1,
    batch_size: int = 3,
    slice_height: int = 640,
    slice_width: int = 640,
    overlap_height_ratio: float = 0.2,
    overlap_width_ratio: float = 0.2,
):
    """
    手動切 patch + 每 n 張 patch 一次 predict + 全圖座標還原 + NMS
    """
    model = get_model()

    t0 = time.perf_counter()

    image = cv2.imread(image_path)
    if image is None:
        raise ValueError(f"Cannot read image: {image_path}")

    t1 = time.perf_counter()

    patch_records = build_patch_records(
        image=image,
        slice_height=slice_height,
        slice_width=slice_width,
        overlap_height_ratio=overlap_height_ratio,
        overlap_width_ratio=overlap_width_ratio,
    )

    t2 = time.perf_counter()

    raw_dets = predict_patch_batches(
        model=model,
        patch_records=patch_records,
        conf=conf,
        batch_size=batch_size,
    )

    t3 = time.perf_counter()

    final_dets = nms_detections(
        raw_dets,
        iou_thres=iou,
        center_dist_thres=18.0,
    )

    t4 = time.perf_counter()

    summary = build_weight_summary(final_dets)

    print(
        f"[SAHI-BATCH] patches={len(patch_records)} "
        f"batch_size={batch_size} "
        f"read={t1-t0:.3f}s slice={t2-t1:.3f}s infer={t3-t2:.3f}s nms={t4-t3:.3f}s total={t4-t0:.3f}s"
    )

    return {
        "detections": final_dets,
        "summary": summary,
    }