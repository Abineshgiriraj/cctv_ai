import os
import re
import time
from collections import defaultdict, deque

import cv2


class AdvancedDetector:
    def __init__(self, config, store, session_id, log):
        self.cfg, self.store, self.session_id, self.log = config, store, session_id, log
        self.models, self.ocr = {}, None
        self.last_violation = defaultdict(float)
        self.last_road_event = defaultdict(float)
        self.helmet_votes = defaultdict(lambda: deque(maxlen=self.cfg.HELMET_CONFIRM_WINDOW))
        self.plate_cache = {}
        self._load_models()
        self._load_ocr()

    def _load_models(self):
        try:
            from ultralytics import YOLO
        except Exception as exc:
            self.log.warning("Advanced detectors unavailable: %s", exc)
            return
        for key, path in {
            "helmet": self.cfg.HELMET_MODEL,
            "plate": self.cfg.PLATE_MODEL,
            "road_damage": self.cfg.ROAD_DAMAGE_MODEL,
            "road_obstruction": self.cfg.ROAD_OBSTRUCTION_MODEL,
        }.items():
            if not path or not os.path.isfile(path):
                self.log.warning("Advanced model missing name=%s path=%s", key, path)
                continue
            try:
                self.models[key] = YOLO(path)
                self.log.info("Advanced model loaded name=%s path=%s classes=%s", key, path, getattr(self.models[key], "names", {}))
            except Exception as exc:
                self.log.warning("Unable to load %s model: %s", key, exc)

    def _load_ocr(self):
        if not self.cfg.OCR_ENABLED:
            return
        try:
            import easyocr
            self.ocr = easyocr.Reader(["en"], gpu=False, verbose=False)
            self.log.info("EasyOCR ready for number-plate reading")
        except Exception as exc:
            self.log.warning("OCR disabled: %s", exc)

    @staticmethod
    def readiness(config):
        return {name: {"configured_path": path, "available": bool(path and os.path.isfile(path))} for name, path in {
            "helmet": config.HELMET_MODEL, "plate": config.PLATE_MODEL,
            "road_damage": config.ROAD_DAMAGE_MODEL, "road_obstruction": config.ROAD_OBSTRUCTION_MODEL}.items()}

    @staticmethod
    def _jpeg(frame):
        if frame is None or frame.size == 0:
            return None
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        return buf.tobytes() if ok else None

    @staticmethod
    def _crop(frame, box, pad=0):
        h, w = frame.shape[:2]
        x1, y1, x2, y2 = [int(v) for v in box]
        x1, y1, x2, y2 = max(0, x1-pad), max(0, y1-pad), min(w, x2+pad), min(h, y2+pad)
        return frame[y1:y2, x1:x2] if x2 > x1 and y2 > y1 else None

    @staticmethod
    def _primary_objects(result, model):
        persons, bikes = [], []
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            return persons, bikes
        for b in boxes:
            try:
                cls_id, conf = int(b.cls[0].item()), float(b.conf[0].item())
                box = [int(v) for v in b.xyxy[0].tolist()]
                track_id = int(b.id[0].item()) if b.id is not None else None
            except Exception:
                continue
            row = {"box": box, "conf": conf, "track_id": track_id}
            if cls_id == 0: persons.append(row)
            elif cls_id == 3: bikes.append(row)
        return persons, bikes

    @staticmethod
    def _rider_matches(person_box, bike_box):
        px1, _, px2, py2 = person_box
        bx1, by1, bx2, by2 = bike_box
        pcx = (px1 + px2) / 2
        bw, bh = bx2-bx1, by2-by1
        return bx1-bw*.65 <= pcx <= bx2+bw*.65 and by1-bh*2.5 <= py2 <= by2+bh*.55

    @staticmethod
    def _helmet_label(name):
        n = str(name).strip().lower().replace("-", "_").replace(" ", "_")
        if any(k in n for k in ("no_helmet", "nohelmet", "without_helmet", "withouthelmet", "helmetless", "barehead", "bare_head", "nohardhat", "no_hardhat")):
            return "no_helmet"
        if "helmet" in n or "hardhat" in n:
            return "helmet"
        return None

    def _helmet_threshold(self, status):
        return self.cfg.NO_HELMET_CONFIDENCE if status == "no_helmet" else self.cfg.HELMET_CONFIDENCE

    @staticmethod
    def _rider_region(frame, bike_box, person_box=None):
        h, w = frame.shape[:2]
        bx1, by1, bx2, by2 = bike_box
        bw, bh = max(1, bx2-bx1), max(1, by2-by1)
        if person_box:
            px1, py1, px2, py2 = person_box
            pw, ph = max(1, px2-px1), max(1, py2-py1)
            box = [px1-int(pw*.3), py1-int(ph*.18), px2+int(pw*.3), py1+int(ph*.68)]
        else:
            box = [bx1-int(bw*.65), by1-int(bh*2.5), bx2+int(bw*.65), by1+int(bh*.55)]
        return [max(0, box[0]), max(0, box[1]), min(w, box[2]), min(h, box[3])]

    def _helmet_status(self, frame, bike, person):
        model = self.models.get("helmet")
        if not model: return None
        region = self._rider_region(frame, bike["box"], person["box"] if person else None)
        crop = self._crop(frame, region, 4)
        if crop is None: return None
        ch, cw = crop.shape[:2]
        scale = 3.0 if max(ch, cw) < 180 else 2.0 if max(ch, cw) < 360 else 1.0
        source = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC) if scale > 1 else crop
        try:
            results = model.predict(source, conf=min(self.cfg.HELMET_CONFIDENCE, self.cfg.NO_HELMET_CONFIDENCE), imgsz=self.cfg.HELMET_IMGSZ, verbose=False)
        except Exception:
            return None
        found = []
        for res in results or []:
            for b in getattr(res, "boxes", []) or []:
                try:
                    cid, conf = int(b.cls[0].item()), float(b.conf[0].item())
                    names = getattr(model, "names", {})
                    name = names.get(cid, cid) if isinstance(names, dict) else names[cid]
                    status = self._helmet_label(name)
                    if not status or conf < self._helmet_threshold(status): continue
                    x1,y1,x2,y2 = [float(v) for v in b.xyxy[0].tolist()]
                    found.append({"status":status, "confidence":conf, "box":[int(region[0]+x1/scale), int(region[1]+y1/scale), int(region[0]+x2/scale), int(region[1]+y2/scale)], "search_box":region})
                except Exception:
                    continue
        if not found: return None
        found.sort(key=lambda d: d["confidence"] + (.08 if d["status"] == "helmet" else 0), reverse=True)
        return found[0]

    def _helmet_confirmed(self, camera_ip, bike, obs):
        key = (camera_ip, bike.get("track_id") if bike.get("track_id") is not None else tuple(int(v/64) for v in bike["box"][:2]))
        votes = self.helmet_votes[key]
        votes.append((obs["status"], obs["confidence"]))
        same = [c for s,c in votes if s == obs["status"]]
        if len(same) < self.cfg.HELMET_CONFIRM_FRAMES: return None
        counts = {s: sum(1 for a,_ in votes if a == s) for s,_ in votes}
        if max(counts, key=counts.get) != obs["status"]: return None
        avg = sum(same)/len(same)
        return (obs["status"], avg) if avg >= self._helmet_threshold(obs["status"]) else None

    @staticmethod
    def _draw(frame, box, label, color):
        x1,y1,x2,y2 = [int(v) for v in box]
        cv2.rectangle(frame, (x1,y1), (x2,y2), color, 2)
        cv2.putText(frame, label, (max(3,x1), max(22,y1-7)), cv2.FONT_HERSHEY_SIMPLEX, .58, color, 2, cv2.LINE_AA)

    def _plate_detect(self, frame, vehicle_box):
        model = self.models.get("plate")
        if not model: return None
        x1,y1,x2,y2 = vehicle_box
        vw,vh = max(1,x2-x1),max(1,y2-y1)
        h,w = frame.shape[:2]
        scope=[max(0,x1-int(vw*.5)),max(0,y1-int(vh*.55)),min(w,x2+int(vw*.5)),min(h,y2+int(vh*.55))]
        crop=self._crop(frame,scope)
        if crop is None:return None
        ch,cw=crop.shape[:2]
        scale=3.0 if max(ch,cw)<250 else 2.0 if max(ch,cw)<500 else 1.0
        source=cv2.resize(crop,None,fx=scale,fy=scale,interpolation=cv2.INTER_CUBIC) if scale>1 else crop
        try:
            results=model.predict(source,conf=self.cfg.PLATE_CONFIDENCE,imgsz=self.cfg.PLATE_IMGSZ,verbose=False)
        except Exception:return None
        best=None
        for res in results or []:
            for b in getattr(res,"boxes",[]) or []:
                try:
                    conf=float(b.conf[0].item()); a,b1,c,d=[float(v) for v in b.xyxy[0].tolist()]
                    box=[int(scope[0]+a/scale),int(scope[1]+b1/scale),int(scope[0]+c/scale),int(scope[1]+d/scale)]
                    pcrop=self._crop(frame,box,4)
                    if pcrop is not None and (best is None or conf>best["confidence"]): best={"confidence":conf,"box":box,"crop":pcrop}
                except Exception:continue
        return best

    @staticmethod
    def _plate_variants(crop):
        if crop is None:return []
        w=crop.shape[1]; scale=min(5.0,max(2.0,320/max(1,w)))
        img=cv2.resize(crop,None,fx=scale,fy=scale,interpolation=cv2.INTER_CUBIC)
        gray=cv2.cvtColor(img,cv2.COLOR_BGR2GRAY)
        clahe=cv2.createCLAHE(2.5,(8,8)).apply(gray)
        blur=cv2.GaussianBlur(clahe,(0,0),1.0); sharp=cv2.addWeighted(clahe,1.8,blur,-.8,0)
        _,otsu=cv2.threshold(sharp,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
        return [img,clahe,sharp,otsu]

    def _ocr_plate(self,crop):
        if self.ocr is None:return None,None
        best=None
        for img in self._plate_variants(crop):
            try: reads=self.ocr.readtext(img,detail=1,paragraph=False,allowlist="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
            except Exception:continue
            for _,raw,score in reads or []:
                text=re.sub(r"[^A-Z0-9]","",str(raw).upper()); score=float(score)
                if not 6<=len(text)<=12 or score<self.cfg.PLATE_OCR_MIN_CONFIDENCE:continue
                bonus=.12 if text.startswith("TN") else 0
                if re.match(r"^[A-Z]{2}\d{1,2}[A-Z]{1,3}\d{4}$",text):bonus+=.35
                rank=score+bonus
                if best is None or rank>best[0]:best=(rank,text,score)
        return (best[1],best[2]) if best else (None,None)

    def _get_plate(self,camera_ip,frame,bike):
        key=(camera_ip,bike.get("track_id") if bike.get("track_id") is not None else tuple(int(v/80) for v in bike["box"][:2]))
        now=time.time(); cached=self.plate_cache.get(key)
        if cached and now-cached["updated_at"]<self.cfg.PLATE_RETRY_SECONDS:return cached
        det=self._plate_detect(frame,bike["box"])
        if det:
            text,ocr_conf=self._ocr_plate(det["crop"]); det["text"]=text; det["ocr_confidence"]=ocr_conf; det["updated_at"]=now
            if text:self.plate_cache[key]=det
            elif cached and cached.get("text") and now-cached["updated_at"]<self.cfg.PLATE_CACHE_SECONDS:return cached
            else:self.plate_cache[key]=det
            return self.plate_cache[key]
        if cached and cached.get("text") and now-cached["updated_at"]<self.cfg.PLATE_CACHE_SECONDS:return cached
        return None

    def _store_no_helmet(self,camera_ip,frame,bike,person,conf,region,plate):
        if self.store is None:return None
        pid=person.get("track_id") if person else None; key=(camera_ip,bike.get("track_id"),pid if pid is not None else -1); now=time.time()
        if now-self.last_violation[key]<self.cfg.VIOLATION_COOLDOWN_SECONDS:return None
        self.last_violation[key]=now
        if person:
            pb=person["box"]; bb=bike["box"]; union=[min(bb[0],pb[0]),min(bb[1],pb[1]),max(bb[2],pb[2]),max(bb[3],pb[3])]
        else:
            bb=bike["box"]; union=[min(bb[0],region[0]),min(bb[1],region[1]),max(bb[2],region[2]),max(bb[3],region[3])]
        evidence=self._crop(frame,union,40)
        return self.store.record_violation(session_id=self.session_id,camera_ip=camera_ip,violation_type="no_helmet",vehicle_type="motorcycle",vehicle_track_id=bike.get("track_id"),person_track_id=pid,helmet_status="no_helmet",plate_number=plate.get("text") if plate else None,plate_confidence=plate.get("ocr_confidence") if plate else None,detection_confidence=conf,evidence_image=self._jpeg(evidence if evidence is not None else frame),plate_image=self._jpeg(plate.get("crop") if plate else None),metadata={"bike_confidence":bike.get("conf"),"helmet_confirm_frames":self.cfg.HELMET_CONFIRM_FRAMES,"plate_detected":bool(plate),"plate_ocr_text":plate.get("text") if plate else None})

    def _run_road_model(self,camera_ip,frame,key,event_type):
        model=self.models.get(key)
        if not model or self.store is None:return []
        try:results=model.predict(frame,conf=self.cfg.ADVANCED_CONFIDENCE,verbose=False)
        except Exception:return []
        events=[]
        for res in results or []:
            for b in getattr(res,"boxes",[]) or []:
                try:
                    cid=int(b.cls[0].item()); conf=float(b.conf[0].item()); box=[int(v) for v in b.xyxy[0].tolist()]; names=getattr(model,"names",{}); label=str(names.get(cid,cid) if isinstance(names,dict) else names[cid])
                except Exception:continue
                k=(camera_ip,event_type,label); now=time.time()
                if now-self.last_road_event[k]<self.cfg.ROAD_EVENT_COOLDOWN_SECONDS:continue
                self.last_road_event[k]=now; crop=self._crop(frame,box,25)
                eid=self.store.record_road_event(session_id=self.session_id,camera_ip=camera_ip,event_type=event_type,model_label=label,confidence=conf,evidence_image=self._jpeg(crop if crop is not None else frame),metadata={"box":box}); events.append(eid)
        return events

    def process(self,camera_ip,clean_frame,primary_result,primary_model,processed_index,draw_frame=None):
        draw_frame=draw_frame if draw_frame is not None else clean_frame
        summary={"helmet_checked":0,"helmet_detected":0,"no_helmet_detected":0,"helmet_violations":0,"plate_detected":0,"plate_read":0,"road_events":0}
        if not self.cfg.ADVANCED_DETECTION_ENABLED or primary_result is None or processed_index%self.cfg.ADVANCED_EVERY_N_FRAMES!=0:return summary
        persons,bikes=self._primary_objects(primary_result,primary_model)
        for bike in bikes:
            rider=next((p for p in persons if self._rider_matches(p["box"],bike["box"])),None)
            plate=self._get_plate(camera_ip,clean_frame,bike) if "plate" in self.models else None
            if plate:
                summary["plate_detected"]+=1; label="PLATE"+(f" {plate['text']}" if plate.get("text") else ""); self._draw(draw_frame,plate["box"],label,(255,160,0)); summary["plate_read"]+=1 if plate.get("text") else 0
            obs=self._helmet_status(clean_frame,bike,rider)
            if not obs:continue
            summary["helmet_checked"]+=1; confirmed=self._helmet_confirmed(camera_ip,bike,obs)
            status,conf=(confirmed if confirmed else (obs["status"],obs["confidence"])); q="" if confirmed else " ?"; color=(0,0,255) if status=="no_helmet" else (0,220,80); self._draw(draw_frame,obs["box"],f"{'NO HELMET' if status=='no_helmet' else 'HELMET'}{q} {conf*100:.0f}%",color)
            if not confirmed:continue
            if status=="helmet":summary["helmet_detected"]+=1; continue
            summary["no_helmet_detected"]+=1
            if self._store_no_helmet(camera_ip,clean_frame,bike,rider,conf,obs["search_box"],plate):summary["helmet_violations"]+=1
        summary["road_events"]+=len(self._run_road_model(camera_ip,clean_frame,"road_damage","road_damage")); summary["road_events"]+=len(self._run_road_model(camera_ip,clean_frame,"road_obstruction","road_obstruction"))
        return summary
