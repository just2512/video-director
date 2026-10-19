"""Bidirectional CompositionIR/film.html mapping with fail-closed unsupported-field handling."""
from __future__ import annotations
import copy, json, re
from urllib.parse import unquote
from video_director import film_document as fd


def _fail(code: str, message: str) -> None:
    raise fd.FilmError(code, message, 422)


def _obj(attrs: dict, name: str) -> dict:
    try:
        value = json.loads(attrs.get(name) or "{}")
    except (TypeError, json.JSONDecodeError):
        _fail("COMPOSITION_METADATA_INVALID", f"{name} 不是有效 JSON")
    if not isinstance(value, dict):
        _fail("COMPOSITION_METADATA_INVALID", f"{name} 必须是 JSON 对象")
    return value


def _style(attrs: dict, cid: str) -> dict:
    values = {}
    for piece in str(attrs.get("style") or "").split(";"):
        if not piece.strip():
            continue
        if ":" not in piece:
            _fail("COMPOSITION_SYNC_UNSUPPORTED", f"片段 {cid} 的 CSS 无法无损映射")
        key, val = piece.split(":", 1)
        key, val = key.strip().lower(), val.strip()
        if key in values:
            _fail("COMPOSITION_SYNC_UNSUPPORTED", f"片段 {cid} 的 CSS 属性 {key} 重复")
        values[key] = val
    extra = set(values) - {"left", "top", "width", "height", "opacity", "object-fit", "transform", "transform-origin"}
    if extra:
        _fail("COMPOSITION_SYNC_UNSUPPORTED", f"片段 {cid} 的 CSS 属性 {sorted(extra)} 没有 IR 对应字段")
    return values


def _css_num(style: dict, name: str, default: float, cid: str) -> float:
    value = style.get(name)
    if value is None:
        return default
    match = re.fullmatch(r"([-+]?(?:\d+(?:\.\d*)?|\.\d+))(?:px)?", value, re.I)
    if not match:
        _fail("COMPOSITION_SYNC_UNSUPPORTED", f"片段 {cid} 的 CSS {name}:{value} 不受支持")
    return float(match.group(1))


def _transform(attrs: dict, stage: dict, cid: str) -> dict | None:
    css = _style(attrs, cid)
    w, h = float(stage["width"]), float(stage["height"])
    left, top = _css_num(css, "left", 0, cid), _css_num(css, "top", 0, cid)
    bw, bh = _css_num(css, "width", w, cid), _css_num(css, "height", h, cid)
    opacity = float(attrs.get("opacity", css.get("opacity", 1)))
    rotation = 0.0
    transform = css.get("transform", "none").strip().lower()
    if transform not in {"", "none"}:
        m = re.fullmatch(r"rotate\(\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+))deg\s*\)", transform)
        if not m:
            _fail("COMPOSITION_SYNC_UNSUPPORTED", f"片段 {cid} 的 CSS transform 不能映射")
        rotation = float(m.group(1))
    if "rotate" in attrs:
        rotation = float(attrs["rotate"])
    origin = css.get("transform-origin", "50% 50%").split()
    if len(origin) != 2:
        _fail("COMPOSITION_SYNC_UNSUPPORTED", f"片段 {cid} 的 transform-origin 不受支持")
    def p(token, axis):
        aliases = ({"left":0,"center":.5,"right":1} if axis == "x" else {"top":0,"center":.5,"bottom":1})
        if token in aliases:
            return aliases[token]
        m = re.fullmatch(r"(\d+(?:\.\d*)?|\.\d+)%", token)
        if m and 0 <= float(m.group(1)) <= 100:
            return float(m.group(1))/100
        _fail("COMPOSITION_SYNC_UNSUPPORTED", f"片段 {cid} 的 transform-origin 值不受支持")
    ax, ay = p(origin[0].lower(), "x"), p(origin[1].lower(), "y")
    sx, sy = bw/w, bh/h
    if abs(sx-sy) > .002:
        _fail("COMPOSITION_SYNC_UNSUPPORTED", f"片段 {cid} 的非等比缩放无法映射为 Transform")
    scale=(sx+sy)/2
    x,y=(left+ax*bw)/w,(top+ay*bh)/h
    anchor={(0,.0):"top_left",(0.5,0):"top_center",(1,0):"top_right",
             (0,.5):"center_left",(.5,.5):"center",(1,.5):"center_right",
             (0,1):"bottom_left",(.5,1):"bottom_center",(1,1):"bottom_right"}.get((ax,ay))
    if anchor is None or not (0<=opacity<=1 and 0<=x<=1 and 0<=y<=1 and .1<=scale<=5 and -3600<=rotation<=3600):
        _fail("COMPOSITION_SYNC_UNSUPPORTED", f"片段 {cid} 的 Transform 超出 IR 约束")
    if abs(x-.5)<1e-6 and abs(y-.5)<1e-6 and abs(scale-1)<1e-6 and abs(rotation)<1e-6 and abs(opacity-1)<1e-6 and anchor=="center":
        return None
    return {"x":x,"y":y,"scale":scale,"rotation":rotation,"opacity":opacity,"anchor":anchor}


def _attrs_allowed(attrs: dict, allowed: set[str], cid: str) -> None:
    extra = sorted(k for k,v in attrs.items() if k not in allowed and v is not None and v is not False)
    if extra:
        _fail("COMPOSITION_SYNC_UNSUPPORTED", f"片段 {cid} 的属性 {extra} 无法映射回 CompositionIR")


def composition_from_film(document: dict) -> dict:
    """Return a validated CompositionIR-shaped dict; never silently drop unmapped edits."""
    meta = (document or {}).get("project_metadata") or {}
    if meta.get("schema") != "video-director-composition-metadata/1":
        _fail("COMPOSITION_SNAPSHOT_MISSING", "film.html 没有 CompositionIR 迁移元数据")
    tracks = document.get("tracks") or []
    baseline = meta.get("film_track_order")
    if not isinstance(baseline, list):
        _fail("COMPOSITION_BINDING_MISSING", "影片缺少轨道顺序基线，无法确认图层叠放顺序")
    ids = [str(t.get("id") or "") for t in tracks]
    baseline = [str(x) for x in baseline]
    if len(ids)!=len(set(ids)) or any(x not in baseline for x in ids) or ids != [x for x in baseline if x in set(ids)]:
        _fail("COMPOSITION_TRACK_ORDER_UNSUPPORTED", "新增或重排的物理轨道没有可逆的 CompositionIR 对应关系")
    ords=[]
    for t in tracks:
        try: ords.append(int((t.get("attrs") or {})["data-vd-order"]))
        except (KeyError,ValueError,TypeError): _fail("COMPOSITION_BINDING_MISSING",f"轨道 {t.get('id')} 缺少顺序绑定")
    if ords != sorted(ords) or len(ords)!=len(set(ords)):
        _fail("COMPOSITION_TRACK_ORDER_UNSUPPORTED","轨道物理顺序已改变")

    project=copy.deepcopy(meta.get("project") or {})
    stage=document.get("stage") or {"width":project.get("width",1920),"height":project.get("height",1080)}
    project.update({"width":int(stage["width"]),"height":int(stage["height"])})
    assets=[]; asset_map={}
    for raw in meta.get("assets") or []:
        row=copy.deepcopy(raw)
        uri=row.pop("file_uri",None)
        if not row.get("id") or not uri:
            _fail("COMPOSITION_ASSET_METADATA_MISSING","资产元数据缺少 id 或安全相对路径")
        row["file"]=unquote(str(uri)); assets.append(row); asset_map[str(row["id"])]=row

    scenes={}
    for row in meta.get("scene_inventory") or []:
        sid=str(row.get("id") or "")
        if not sid or sid in scenes: _fail("COMPOSITION_METADATA_INVALID","scene_inventory 有空 ID 或重复项")
        scenes[sid]={"index":int(row["index"]),"start":float(row["start"]),"duration":float(row["duration"]),
                     "layer_count":int(row.get("layer_count", 0)),
                     "layer_indexes":(set(row["layer_indexes"]) if "layer_indexes" in row
                                     else set(range(int(row.get("layer_count", 0)))),
                     "metadata":copy.deepcopy(row.get("metadata") or {}),"layers":[],"layer_ids":set()}
    if not scenes: _fail("COMPOSITION_METADATA_MISSING","scene_inventory 缺失")
    lanes=copy.deepcopy(meta.get("audio_tracks") or [])
    lane_map={str(x.get("id")):x for x in lanes if isinstance(x,dict) and x.get("id")}
    lane_state={}; lane_name={}; overlays=[]; audio=[]; overlay_ids=set(); overlay_indexes=set(); audio_ids=set()
    max_end=0.0; subtitle_seen=False

    def asset_for(a,cid,allowed):
        aid=str(a.get("data-asset-id") or "")
        if aid not in asset_map: _fail("COMPOSITION_ASSET_METADATA_MISSING",f"片段 {cid} 缺少有效素材绑定")
        row=asset_map[aid]
        if str(row.get("type") or "").lower() not in allowed: _fail("COMPOSITION_ASSET_TYPE_MISMATCH",f"片段 {cid} 与素材类型不一致")
        path=unquote(str(a.get("src") or "").split("#",1)[0])
        if path != str(row.get("file") or ""): _fail("COMPOSITION_ASSET_REFERENCE_CHANGED",f"片段 {cid} 的 src 与素材 ID {aid} 不匹配")
        source=fd._source_range(str(a.get("src") or ""))
        if source[1] is None: _fail("COMPOSITION_SYNC_UNSUPPORTED",f"片段 {cid} 缺少源结束时间")
        return aid,row,source

    for track in tracks:
        ta=track.get("attrs") or {}; role=str(ta.get("data-vd-role") or ""); clips=track.get("clips") or []
        if role not in {"scene-layer","overlay","audio","subtitle"}:
            _fail("COMPOSITION_TRACK_BINDING_MISSING",f"轨道 {track.get('id')} 没有 CompositionIR 绑定")
        if "hidden" in ta and role!="audio": _fail("COMPOSITION_SYNC_UNSUPPORTED",f"轨道 {track.get('id')} 的 hidden 状态无法映射")
        if role=="scene-layer":
            if len(clips)>1: _fail("COMPOSITION_TRACK_BINDING_MISSING",f"主画面轨道 {track.get('id')} 包含多个片段")
            if not clips: continue
            clip=clips[0]; a=clip.get("attrs") or {}; tag=clip.get("tag"); cid=str(clip.get("id") or "?")
            _attrs_allowed(a,{"id","src","at","speed","style","rotate","opacity","data-asset-id","data-vd-scene-id","data-vd-scene-index","data-vd-scene-start","data-vd-scene-duration","data-vd-layer-index","data-vd-scene-meta","data-vd-layer-meta"},cid)
            sid=str(a.get("data-vd-scene-id") or "")
            if sid!=str(ta.get("data-vd-scene-id") or "") or str(ta.get("data-vd-layer-index") or "")!=str(a.get("data-vd-layer-index") or ""):
                _fail("COMPOSITION_BINDING_CONFLICT",f"片段 {cid} 与物理轨道的场景/图层绑定不一致")
            try: si,li=int(a["data-vd-scene-index"]),int(a["data-vd-layer-index"]); bs=float(a["data-vd-scene-start"]); bd=float(a["data-vd-scene-duration"])
            except (KeyError,ValueError,TypeError): _fail("COMPOSITION_BINDING_MISSING",f"片段 {cid} 缺少场景时间/索引元数据")
            if sid not in scenes: _fail("COMPOSITION_BINDING_MISSING",f"片段 {cid} 引用未知场景 {sid}")
            ent=scenes[sid]
            if si!=ent["index"] or abs(bs-ent["start"])>1e-6 or abs(bd-ent["duration"])>1e-6 or _obj(a,"data-vd-scene-meta")!=ent["metadata"]:
                _fail("COMPOSITION_METADATA_CONFLICT",f"片段 {cid} 的场景元数据与项目快照冲突")
            layer=_obj(a,"data-vd-layer-meta"); aid,asset,source=asset_for(a,cid,{"video","image"})
            expected="video" if asset["type"]=="video" else "img"
            if tag!=expected: _fail("COMPOSITION_ASSET_TYPE_MISMATCH",f"片段 {cid} 的标签与素材类型不一致")
            if tag=="img" and "speed" in a: _fail("COMPOSITION_SYNC_UNSUPPORTED",f"图片片段 {cid} 不支持 speed")
            dur=fd.clip_duration(clip)
            if dur is None or dur<=0: _fail("COMPOSITION_SYNC_UNSUPPORTED",f"片段 {cid} 没有有效时长")
            fit=_style(a,cid).get("object-fit","cover")
            if fit not in {"cover","contain"}: _fail("COMPOSITION_SYNC_UNSUPPORTED",f"片段 {cid} 的 object-fit 无法映射")
            layer.update({"type":"video" if tag=="video" else "image","asset_id":aid,
                "start":max(0.0,fd._number(a.get("at",0),"at",0)-ent["start"]),"duration":dur,
                "speed":fd._number(a.get("speed",1),"speed",.25,4) if tag=="video" else 1.0,
                "source_in":source[0] if tag=="video" else 0.0,"source_out":source[1] if tag=="video" else None,
                "fit_mode":fit,"transform":_transform(a,stage,cid)})
            if "locked" in ta: layer["locked"]=True
            elif layer.get("locked") is True: layer["locked"]=False
            at=fd._number(a.get("at",0),"at",0)
            if li in ent["layer_ids"]: _fail("COMPOSITION_LAYER_BINDING_DUPLICATE",f"场景 {sid} 的图层索引 {li} 重复")
            ent["layer_ids"].add(li); ent["layers"].append((li,at,at+dur,layer)); max_end=max(max_end,at+dur)

        elif role=="overlay":
            if any(x in ta for x in ("locked","hidden","muted")): _fail("COMPOSITION_SYNC_UNSUPPORTED",f"叠加轨道 {track.get('id')} 的轨道状态无法映射")
            for clip in clips:
                a=clip.get("attrs") or {}; tag=clip.get("tag"); cid=str(clip.get("id") or "?")
                _attrs_allowed(a,{"id","src","at","speed","style","opacity","data-asset-id","data-vd-overlay-id","data-vd-overlay-index","data-vd-overlay-meta","data-vd-track-id"},cid)
                oid=str(a.get("data-vd-overlay-id") or "")
                if not oid or oid in overlay_ids or oid!=str(ta.get("data-vd-overlay-id") or ""): _fail("COMPOSITION_OVERLAY_BINDING_CONFLICT",f"叠加片段 {cid} 的稳定 ID 无效")
                raw_index=a.get("data-vd-overlay-index",ta.get("data-vd-overlay-index"))
                try: overlay_index=int(raw_index) if raw_index is not None else len(overlays)
                except (TypeError,ValueError): _fail("COMPOSITION_OVERLAY_BINDING_CONFLICT",f"叠加片段 {cid} 的原始顺序索引无效")
                if overlay_index<0 or overlay_index in overlay_indexes: _fail("COMPOSITION_OVERLAY_BINDING_CONFLICT",f"叠加片段 {cid} 的原始顺序索引重复或为负数")
                try:
                    track_overlay_index = int(ta["data-vd-overlay-index"]) if ta.get("data-vd-overlay-index") is not None else overlay_index
                except (TypeError,ValueError):
                    _fail("COMPOSITION_OVERLAY_BINDING_CONFLICT",f"叠加轨道 {track.get('id')} 的顺序索引无效")
                if track_overlay_index != overlay_index: _fail("COMPOSITION_BINDING_CONFLICT",f"叠加片段 {cid} 的轨道与片段顺序索引冲突")
                overlay_indexes.add(overlay_index)
                overlay_ids.add(oid); ometa=_obj(a,"data-vd-overlay-meta"); aid,asset,source=asset_for(a,cid,{"video","image"})
                if tag!=("video" if asset["type"]=="video" else "img"): _fail("COMPOSITION_ASSET_TYPE_MISMATCH",f"叠加片段 {cid} 的标签与素材类型不一致")
                if tag=="img" and "speed" in a: _fail("COMPOSITION_SYNC_UNSUPPORTED",f"图片叠加片段 {cid} 不支持 speed")
                dur=fd.clip_duration(clip)
                if dur is None or dur<=0: _fail("COMPOSITION_SYNC_UNSUPPORTED",f"叠加片段 {cid} 没有有效时长")
                css=_style(a,cid); fit=css.get("object-fit","cover")
                if fit not in {"cover","contain"}: _fail("COMPOSITION_SYNC_UNSUPPORTED",f"叠加片段 {cid} 的 fit_mode 不受支持")
                w,h=float(stage["width"]),float(stage["height"]); ov=copy.deepcopy(ometa)
                ov.update({"id":oid,"asset_id":aid,"start":fd._number(a.get("at",0),"at",0),"duration":dur,
                    "source_in":source[0],"speed":fd._number(a.get("speed",1),"speed",.25,4) if tag=="video" else 1.0,
                    "track_id":str(ta.get("data-vd-track-id") or ometa.get("track_id") or "overlay-main"),"fit_mode":fit,
                    "x":_css_num(css,"left",0,cid)/w,"y":_css_num(css,"top",0,cid)/h,
                    "width":_css_num(css,"width",w,cid)/w,"height":_css_num(css,"height",h,cid)/h,
                    "opacity":fd._number(a.get("opacity",css.get("opacity",ometa.get("opacity",1))),"opacity",0,1)})
                if ometa.get("transform") is not None or css.get("transform") not in {None,"","none"} or "rotate" in a:
                    if ov.get("presentation_mode")!="full_frame": _fail("COMPOSITION_SYNC_UNSUPPORTED",f"叠加层 {oid} 的 Transform 不适用于当前 presentation_mode")
                    ov["transform"]=_transform(a,stage,cid)
                overlays.append((overlay_index,ov)); max_end=max(max_end,float(ov["start"])+dur)

        elif role=="audio":
            lane_id=str(ta.get("data-vd-audio-lane-id") or "")
            if lane_id:
                lane=lane_map.get(lane_id)
                if lane is None or _obj(ta,"data-vd-audio-lane-meta")!=lane: _fail("COMPOSITION_AUDIO_LANE_MISSING",f"音频轨道 {track.get('id')} 的 lane 元数据不一致")
                state={"locked":"locked" in ta,"hidden":"hidden" in ta,"muted":"muted" in ta}
                if lane_id in lane_state and lane_state[lane_id]!=state: _fail("COMPOSITION_LANE_STATE_CONFLICT",f"音频 lane {lane_id} 的物理轨道状态冲突")
                lane_state[lane_id]=state; label=str(ta.get("data-label") or lane.get("name") or "")
                if lane_id in lane_name and lane_name[lane_id]!=label: _fail("COMPOSITION_LANE_NAME_CONFLICT",f"音频 lane {lane_id} 名称不一致")
                lane_name[lane_id]=label
            elif clips: _fail("COMPOSITION_AUDIO_LANE_MISSING",f"音频轨道 {track.get('id')} 缺少 lane ID")
            for clip in clips:
                a=clip.get("attrs") or {}; tag=clip.get("tag"); cid=str(clip.get("id") or "?")
                _attrs_allowed(a,{"id","src","at","speed","volume","muted","data-role","data-asset-id","data-vd-audio-id","data-vd-audio-meta"},cid)
                aid_id=str(a.get("data-vd-audio-id") or "")
                if not aid_id or aid_id in audio_ids: _fail("COMPOSITION_AUDIO_BINDING_CONFLICT",f"音频片段 {cid} 的 ID 无效或重复")
                audio_ids.add(aid_id); ameta=_obj(a,"data-vd-audio-meta"); aid,asset,source=asset_for(a,cid,{"audio","video"})
                if tag!="audio": _fail("COMPOSITION_SYNC_UNSUPPORTED",f"音频片段 {cid} 的标签必须为 audio")
                dur=fd.clip_duration(clip)
                if dur is None or dur<=0: _fail("COMPOSITION_SYNC_UNSUPPORTED",f"音频片段 {cid} 没有有效时长")
                item=copy.deepcopy(ameta)
                item.update({"id":ameta.get("id") if "id" in ameta else aid_id,"type":str(a.get("data-role") or "audio"),"track_id":lane_id or None,"asset_id":aid,
                    "start":fd._number(a.get("at",0),"at",0),"duration":dur,"source_in":source[0],"source_out":source[1],
                    "speed":fd._number(a.get("speed",1),"speed",.25,4),"volume":fd._number(a.get("volume",1),"volume",0,2)})
                item["muted"]=bool(ameta.get("muted",False)) if lane_id and lane_state.get(lane_id,{}).get("muted") else ("muted" in a)
                audio.append(item); max_end=max(max_end,float(item["start"])+dur)

        elif role=="subtitle":
            if len(clips)>1: _fail("COMPOSITION_SUBTITLE_BINDING_CONFLICT","字幕轨道只能有一个 iframe")
            if not clips: continue
            clip=clips[0]; a=clip.get("attrs") or {}; cid=str(clip.get("id") or "?")
            _attrs_allowed(a,{"id","src","at","style","data-vd-subtitle-meta","data-vd-subtitle-style"},cid)
            src=str(a.get("src") or ""); path=unquote(src.split("#",1)[0]); ran=fd._source_range(src)
            if clip.get("tag")!="iframe" or path!="scenes/subtitles.html" or ran[0]!=0 or ran[1] is None:
                _fail("COMPOSITION_SYNC_UNSUPPORTED","字幕 iframe 的类型、路径或源区间已改变")
            if abs(fd._number(a.get("at",0),"subtitle at",0))>1e-6: _fail("COMPOSITION_SYNC_UNSUPPORTED","字幕 iframe 被移离 0 秒")
            expected=f"left:0px;top:0px;width:{stage['width']}px;height:{stage['height']}px"
            if str(a.get("style") or "").replace(" ","")!=expected.replace(" ",""): _fail("COMPOSITION_SYNC_UNSUPPORTED","字幕 iframe 画布几何已改变")
            sub=meta.get("subtitle_track")
            if not sub: _fail("COMPOSITION_METADATA_MISSING","字幕 iframe 存在但 subtitle_track 快照缺失")
            if _obj(a,"data-vd-subtitle-meta")!={k:v for k,v in sub.items() if k!="cues"}: _fail("COMPOSITION_METADATA_CONFLICT","字幕 track 元数据发生变化")
            if _obj(a,"data-vd-subtitle-style")!=(meta.get("subtitle_style") or {}): _fail("COMPOSITION_METADATA_CONFLICT","字幕样式元数据发生变化")
            subtitle_seen=True
            if fd.clip_end(clip) is not None: max_end=max(max_end,fd.clip_end(clip))

    for lane_id,state in lane_state.items():
        lane_map[lane_id].update(state)
        if lane_id in lane_name: lane_map[lane_id]["name"]=lane_name[lane_id]
    scene_list=[]
    for sid,entry in scenes.items():
        rows=sorted(entry["layers"],key=lambda row:row[0])
        expected_layer_ids=entry["layer_indexes"]
        if entry["layer_ids"] != expected_layer_ids:
            missing=sorted(expected_layer_ids-entry["layer_ids"])
            extra=sorted(entry["layer_ids"]-expected_layer_ids)
            _fail("COMPOSITION_SCENE_LAYER_BINDING_MISSING",
                  f"场景 {sid} 的原始图层绑定不完整（缺少索引 {missing}，额外索引 {extra}）；"
                  "删除图层需要显式 IR 删除/重排契约，不能凭空补造素材")
        if not rows:
            # An all-gap scene is only representable when another track keeps the film document alive.
            start=entry["start"]
            duration=max(entry["duration"],0.001)
            scene=copy.deepcopy(entry["metadata"])
            scene.update({"id":sid,"start":start,"duration":duration,"layers":[{
                "type":"gap","asset_id":None,"start":0.0,"duration":duration,
                "speed":1.0,"source_in":0.0,"source_out":None,"fit_mode":"cover",
            }]})
            scene_list.append((entry["index"],scene))
            max_end=max(max_end,start+duration)
            continue
        start=min(entry["start"],min(row[1] for row in rows))
        duration=max(entry["duration"],max(row[2] for row in rows)-start,.001)
        scene=copy.deepcopy(entry["metadata"]); scene.update({"id":sid,"start":start,"duration":duration,"layers":[]})
        cursor=0.0
        for _,at,_end,layer in rows:
            layer_start=max(0.0,at-start)
            if layer_start < cursor - 0.01:
                _fail("COMPOSITION_SCENE_OVERLAP",
                      f"场景 {sid} 的图层在影片时间线上相互重叠，当前 CompositionIR 场景模型无法无损表达")
            if layer_start > cursor + 0.01:
                gap_duration=layer_start-cursor
                scene["layers"].append({
                    "type":"gap","asset_id":None,"start":cursor,"duration":gap_duration,
                    "speed":1.0,"source_in":0.0,"source_out":None,"fit_mode":"cover",
                })
                cursor=layer_start
            layer["start"]=layer_start
            scene["layers"].append(layer)
            cursor=layer_start+float(layer["duration"])
        if duration > cursor + 0.01:
            scene["layers"].append({
                "type":"gap","asset_id":None,"start":cursor,"duration":duration-cursor,
                "speed":1.0,"source_in":0.0,"source_out":None,"fit_mode":"cover",
            })
            cursor=duration
        scene_list.append((entry["index"],scene)); max_end=max(max_end,start+duration)
    project["duration"]=max(fd._number(project.get("duration",.001),"project duration",.001),max_end)
    result={"version":meta.get("version",1),"project":project,"style_id":meta.get("style_id"),
        "scenes":[x[1] for x in sorted(scene_list,key=lambda x:x[0])],"audio":audio,"audio_tracks":lanes,
        "watermarks":copy.deepcopy(meta.get("watermarks") or []),"visual_tracks":copy.deepcopy(meta.get("visual_tracks") or []),
        "visual_overlays":[value for _index,value in sorted(overlays,key=lambda item:item[0])],"subtitle_style":copy.deepcopy(meta.get("subtitle_style")),
        "subtitle_track":copy.deepcopy(meta.get("subtitle_track")) if subtitle_seen else None,
        "origin_template_id":meta.get("origin_template_id"),"origin_template_version":meta.get("origin_template_version"),
        "active_variant_id":meta.get("active_variant_id"),"variants":copy.deepcopy(meta.get("variants") or []),
        "references":copy.deepcopy(meta.get("references")),"style_contract":copy.deepcopy(meta.get("style_contract")),
        "camera_tracks":copy.deepcopy(meta.get("camera_tracks") or []),"continuity_links":copy.deepcopy(meta.get("continuity_links") or []),
        "assets":assets}
    try:
        from video_director.models import CompositionIR
        return json.loads(CompositionIR.model_validate(result).model_dump_json())
    except Exception as exc:
        _fail("COMPOSITION_SYNC_INVALID",f"反向映射结果未通过 CompositionIR 校验：{exc}")
