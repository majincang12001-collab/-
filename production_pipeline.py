"""Modular, provider-agnostic production contracts for AI manhua episodes.

This layer deliberately separates creative planning from model execution.
It can import the existing legacy storyboard format without changing old projects.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import json
import re
import time


PIPELINE_VERSION = "1.10"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _slug(value: Any, fallback: str) -> str:
    raw = re.sub(r"[^0-9A-Za-z_-]+", "_", _text(value)).strip("_")
    return raw or fallback


def build_project_spec(production: dict, project: dict | None = None) -> dict:
    shots = production.get("storyboard") or []
    aspect = _text(production.get("aspect_ratio") or "16:9")
    if aspect not in ("16:9", "9:16", "3:4"):
        aspect = "16:9"
    width, height = {"16:9": (1920, 1080), "9:16": (1080, 1920), "3:4": (1440, 1920)}[aspect]
    return {
        "schema_version": 1,
        "pipeline_version": PIPELINE_VERSION,
        "project_id": _text(production.get("project_id") or (project or {}).get("id")),
        "title": _text((project or {}).get("name") or production.get("title") or "未命名漫剧"),
        "format": {"type": "motion_comic", "aspect_ratio": aspect,
                   "target_resolution": {"width": width, "height": height}, "fps": 24},
        "visual_style": _text(production.get("style") or "Chinese anime donghua, cinematic manhua"),
        "story_source": {"text": _text(production.get("story")), "shot_count": len(shots)},
        "review_policy": {"human_approval_required_before_video": False,
                          "production_mode": "auto_first",
                          "automatic_checks": ["file_integrity", "prompt_completeness", "character_anchor_presence", "scene_continuity_metadata"],
                          "human_review_scope": "critical_shots_and_low_confidence_exceptions",
                          "no_silent_fallback": True, "preserve_generation_provenance": True},
    }


def _scene_key_for_shot(shot: dict) -> str:
    explicit = _text(shot.get("scene_key") or shot.get("scene_id") or shot.get("location_id"))
    if explicit:
        return _slug(explicit, "scene_unknown")
    text = " ".join(_text(shot.get(k)) for k in ("location", "scene", "visual", "action"))
    mentions_tree = any(k in text for k in ("柿子树", "枯枝", "树杈", "柿子", "乌鸦"))
    mentions_graveyard = any(k in text for k in ("乱坟岗", "坟地", "墓碑", "墓前", "香"))
    # A reveal/descending transition that ends at the graveyard belongs to the
    # destination scene, not the tree scene merely because the tree is mentioned.
    if mentions_tree and mentions_graveyard and any(k in text for k in ("向下", "带出", "最终落在", "下降", "俯冲")):
        return "graveyard_night"
    if mentions_tree:
        return "persimmon_tree_night"
    if mentions_graveyard:
        return "graveyard_night"
    return "scene_unknown"


def _scene_anchor_for_key(key: str) -> str:
    return {
        "persimmon_tree_night": "同一棵枯萎柿子树，扭曲干枯枝干与腐烂柿子位置关系连续；深夜乌云遮月，冷青夜光与湿冷薄雾统一",
        "graveyard_night": "同一片荒凉乱坟岗夜景，墓碑高低错落、潮湿泥土与低位薄雾统一；墓碑朝向和祭拜区域空间关系连续",
    }.get(key, "")


def derive_prop_states(storyboard: list[dict]) -> list[dict]:
    """Derive explicit per-shot prop states from story order; never infer a future reveal early."""
    states = []
    incense_state = "not_introduced"
    pendant_state = "not_introduced"
    grave_marker_state = "not_introduced"
    for index, shot in enumerate(storyboard or [], 1):
        text = " ".join(_text(shot.get(k)) for k in ("visual", "action", "prompt", "dialogue"))
        warnings = []
        if any(k in text for k in ("墓碑", "墓前", "坟墓")):
            grave_marker_state = "present_same_marker"
        if any(k in text for k in ("香", "香火", "香烛")):
            if any(k in text for k in ("突然自行燃起", "无风自燃", "香火自燃")):
                incense_state = "self_ignited"
            elif any(k in text for k in ("燃起", "自燃", "点燃", "火苗", "燃烧")) and not any(k in text for k in ("未燃", "没有燃")):
                incense_state = "self_ignited" if any(k in text for k in ("突然", "自行")) else "lit"
            elif any(k in text for k in ("未燃", "没有燃", "没有火", "熄灭", "灰暗", "无火")):
                incense_state = "unlit"
            elif any(k in text for k in ("插", "放在", "摆放")):
                incense_state = "placed_unlit"
            elif any(k in text for k in ("抱着", "拿着", "手里")) and any(k in text for k in ("香", "香火", "香烛")):
                if incense_state == "placed_unlit" and not any(k in text for k in ("拔起", "重新拿起", "取下")):
                    warnings.append("香已在前序镜头插于墓前，本镜头文字又写人物抱着香；按连续性保持香在墓前，需核对剧本文字")
                else:
                    incense_state = "carried_unlit"
        if any(k in text for k in ("玉佩", "龙形玉")):
            pendant_state = "revealed_and_held" if any(k in text for k in ("拿起", "握住", "拿着")) else "revealed_on_ground"
        # Story-order guard for this episode's explicit late reveal: do not let a
        # generic prompt mention cause the pendant to appear before shot 25.
        if index < 25 and pendant_state == "not_introduced":
            pendant_state = "absent"
        row = {"grave_marker": grave_marker_state, "incense": incense_state, "dragon_jade_pendant": pendant_state, "warnings": warnings}
        states.append(row)
    return states


def prop_state_anchors(states: dict) -> list[str]:
    """Human-readable visual constraints for the exact prop state in one shot."""
    anchors = []
    if states.get("grave_marker") == "present_same_marker":
        anchors.append("墓碑沿用同一块旧石碑的材质、裂痕、朝向和位置，不得换成新墓碑")
    incense = states.get("incense")
    if incense == "carried_unlit": anchors.append("三炷香仍由人物手持，数量固定三支，香头完全未燃，不得提前出现火苗或烟")
    elif incense == "placed_unlit": anchors.append("三炷香已插放在墓前，数量固定三支，香头灰暗未燃，无火星、无烟")
    elif incense == "unlit": anchors.append("三炷香保持熄灭状态，香头灰暗无火星、无烟，不得自行点燃")
    elif incense in ("lit", "self_ignited"): anchors.append("同一组三炷香保持燃烧状态，火苗与青烟方向连续，不得增加香枝数量")
    anchors.extend(states.get("warnings") or [])
    pendant = states.get("dragon_jade_pendant")
    if pendant == "absent": anchors.append("龙形玉佩尚未登场，画面中绝对不得出现玉佩、龙形挂件或类似替代道具")
    elif pendant == "revealed_on_ground": anchors.append("同一枚暗青色龙形玉佩刚出现在韩额头前方地面，盘龙雕纹和微红龙眼固定，尚未被拿起")
    elif pendant == "revealed_and_held": anchors.append("韩手中拿着同一枚暗青色龙形玉佩，盘龙雕纹、磨损边缘和微红龙眼保持一致")
    return anchors


def _prop_keys_for_shot(shot: dict) -> list[str]:
    text = " ".join(_text(shot.get(k)) for k in ("visual", "action", "prompt"))
    keys = []
    if "玉佩" in text or "玉" in text: keys.append("dragon_jade_pendant")
    if any(k in text for k in ("香", "香火", "香烛")): keys.append("three_incense_sticks")
    if "墓碑" in text: keys.append("grave_marker")
    if "纸钱" in text: keys.append("joss_paper")
    return keys


def build_asset_bible(production: dict, project_files: list[dict] | None = None) -> dict:
    characters = []
    for i, char in enumerate(production.get("characters") or [], 1):
        name = _text(char.get("name"))
        if not name:
            continue
        matched_refs = list(char.get("reference_images") or [])
        declared_ref = _text(char.get("reference_file"))
        if declared_ref:
            matched_refs.append(declared_ref)
        # The explicitly approved episode base image belongs to the protagonist
        # only. Never fan it out to supporting cast members.
        if production.get("approved_base_image") and production.get("base_image_file"):
            hero_name = _text((production.get("hero") or {}).get("name"))
            if not hero_name:
                hero_name = next((_text(x.get("name")) for x in (production.get("characters") or []) if _text(x.get("role")) == "主角"), "")
            is_protagonist = _text(char.get("role")) == "主角"
            name_matches_hero = bool(hero_name and (name == hero_name or name in hero_name or hero_name in name))
            if is_protagonist or name_matches_hero:
                matched_refs.append(_text(production.get("base_image_file")))
        # Never infer a character reference from a filename containing a person's
        # name: the file may be a prop, inscription, tombstone, or unrelated upload.
        # References must be explicitly attached to this character in production data.
        # When a real project file inventory is supplied, retain only references
        # that actually belong to this project. Never infer a match from an opaque filename.
        if project_files is not None:
            known = {_text(x.get("stored") or x.get("name")) for x in project_files}
            matched_refs = [x for x in matched_refs if x in known]
        role = _text(char.get("role") or "配角")
        entity_type = _text(char.get("entity_type"))
        if not entity_type:
            if any(token in (name + role) for token in ("乌鸦", "动物", "兽", "鸟")):
                entity_type = "animal"
            elif "墓碑" in _text(char.get("visual_lock")) or "不生成实体" in _text(char.get("negative_constraints")):
                entity_type = "memorial_only"
            else:
                entity_type = "human"
        if entity_type == "memorial_only":
            # A memorial-only record can never own or lend a human portrait reference.
            matched_refs = []
        identity_anchors = ([x for x in [_text(char.get("visual_lock")), _text(char.get("negative_constraints"))] if x]
                            if entity_type == "memorial_only" else
                            [x for x in [_text(char.get("appearance")), _text(char.get("costume")),
                                         _text(char.get("visual_lock")), _text(char.get("identity_anchor"))] if x])
        characters.append({
            "id": _slug(char.get("character_id") or name, f"character_{i}"), "name": name,
            "role": role, "entity_type": entity_type,
            "age": _text(char.get("age")), "identity_anchors": identity_anchors,
            "appearance": _text(char.get("appearance")), "costume": _text(char.get("costume")),
            "personality": _text(char.get("personality")),
            "visual_lock": _text(char.get("visual_lock")),
            "negative_constraints": _text(char.get("negative_constraints")),
            "voice_profile": _text(char.get("voice_profile")),
            "scene_scope": _text(char.get("scene_key") or char.get("scene_scope")),
            "continuity_note": _text(char.get("continuity_note")),
            "reference_images": list(dict.fromkeys(matched_refs)),
            "reference_status": "linked" if matched_refs else "missing",
            "reference_warning": "角色尚无已核实的专属参考图；当前仅使用文字锚点，不能宣称图像身份已锁定" if not matched_refs else "",
            "continuity_policy": "reference_first; same identity across shots in same scene; flag drift; do not invent missing traits",
        })
    scenes_by_key = {}
    for shot in production.get("storyboard") or []:
        key = _scene_key_for_shot(shot)
        if key == "scene_unknown":
            continue
        row = scenes_by_key.setdefault(key, {"id": key, "name": {"persimmon_tree_night":"枯柿子树区域", "graveyard_night":"乱坟岗墓区"}.get(key, _text(shot.get("scene") or shot.get("location") or key)), "canonical_description": _scene_anchor_for_key(key) or _text(shot.get("scene_description") or shot.get("location"))})
        if not row.get("canonical_description"):
            row["canonical_description"] = _text(shot.get("scene_description") or shot.get("location"))
    prop_catalog = {
        "dragon_jade_pendant": {"id":"dragon_jade_pendant","name":"暗青色龙形玉佩","canonical_description":"同一枚暗青色龙形玉佩，盘龙雕纹、磨损边缘与微弱泛红龙眼保持一致，不改变形状和颜色"},
        "three_incense_sticks": {"id":"three_incense_sticks","name":"墓前三炷香","canonical_description":"同一组墓前三炷香，数量固定为三，插放位置和火头状态严格服从本镜头动作，不得擅自增加或提前点燃"},
        "grave_marker": {"id":"grave_marker","name":"旧墓碑","canonical_description":"同一块旧石碑，石材颜色、裂痕和朝向一致；许清月姓名只在剧情指定镜头清晰出现"},
        "joss_paper": {"id":"joss_paper","name":"纸钱","canonical_description":"纸钱只出现在墓前地面指定位置，不变成其他道具"},
    }
    used_props = {key for shot in (production.get("storyboard") or []) for key in _prop_keys_for_shot(shot)}
    props = list(production.get("props") or []) + [prop_catalog[k] for k in sorted(used_props) if k not in {str(x.get("id") or x.get("name")) for x in (production.get("props") or []) if isinstance(x, dict)}]
    scenes = list(scenes_by_key.values())
    return {"schema_version": 1, "characters": characters, "locations": scenes, "scenes": scenes,
            "props": props, "style_rules": [
                _text(production.get("style") or "Chinese anime donghua, cinematic manhua"),
                "one shot must have one clear visual subject and one dominant action",
                "reuse canonical character and costume anchors across shots",
            ]}


def _cast_names(shot: dict) -> list[str]:
    """Normalize all legacy cast fields without dropping the singular `character` field."""
    raw = next((shot.get(key) for key in ("characters", "character_names", "character", "character_name") if shot.get(key)), [])
    if isinstance(raw, str):
        values = re.split(r"[、，,；;|]", raw)
    elif isinstance(raw, list):
        values = raw
    else:
        values = []
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def build_shot_plan(production: dict) -> dict:
    shots = []
    storyboard = production.get("storyboard") or []
    states_by_index = derive_prop_states(storyboard)
    for i, old in enumerate(storyboard, 1):
        sid = _slug(old.get("shot_id") or f"shot_{int(old.get('id') or i):04d}", f"shot_{i:04d}")
        shots.append({
            "shot_id": sid, "order": i, "scene_id": _scene_key_for_shot(old),
            "shot_type": _text(old.get("shot_type") or "中景"),
            "visual": _text(old.get("visual") or old.get("description")),
            "action": _text(old.get("action")), "dialogue": _text(old.get("dialogue") or old.get("narration")),
            "camera": {"movement": _text(old.get("camera_movement") or "subtle_push"),
                       "composition": _text(old.get("composition") or old.get("shot_type") or "中景")},
            "character_ids": list(old.get("character_ids") or []),
            "character_names": _cast_names(old),
            "scene_key": _scene_key_for_shot(old), "scene_anchor": _scene_anchor_for_key(_scene_key_for_shot(old)),
            "prop_keys": _prop_keys_for_shot(old),
            "prop_states": states_by_index[i-1],
            "prop_state_anchors": prop_state_anchors(states_by_index[i-1]),
            "prompt": _text(old.get("prompt")), "negative_prompt": _text(old.get("negative_prompt")),
            "duration_seconds": float(old.get("duration_seconds") or 4.0),
            "status": _text(old.get("status") or "planned"),
            "image_file": _text(old.get("image_file") or old.get("keyframe_file")),
            "visual_review": old.get("visual_review") if isinstance(old.get("visual_review"), dict) else {"status": "pending", "notes": ""},
            "source_legacy_id": old.get("id", i),
        })
    return {"schema_version": 1, "shots": shots}


def build_generation_brief(shot_plan: dict, asset_bible: dict, provider: str = "auto") -> dict:
    chars = asset_bible.get("characters") or []
    scenes = asset_bible.get("scenes") or asset_bible.get("locations") or []
    props = asset_bible.get("props") or []
    jobs = []
    for shot in shot_plan.get("shots", []):
        ids = set(shot.get("character_ids") or [])
        names = set(shot.get("character_names") or [])
        # Cast is a semantic decision, never a text search. A name on a prop,
        # gravestone, letter, screen, or dialogue must not summon that person.
        assigned = [c for c in chars if (ids and c["id"] in ids) or (names and c["name"] in names)]
        memorial_only = [c for c in assigned if c.get("entity_type") == "memorial_only"]
        matched = [c for c in assigned if c.get("entity_type") != "memorial_only"]
        prompt_parts = [shot.get("prompt", "")]
        scene = next((x for x in scenes if x.get("id") == shot.get("scene_key")), None)
        if scene and scene.get("canonical_description"):
            prompt_parts.append("场景连续性锚点：" + scene["canonical_description"])
        prop_map = {str(x.get("id") or x.get("name")): x for x in props if isinstance(x, dict)}
        for prop_key in shot.get("prop_keys", []):
            prop = prop_map.get(prop_key)
            if prop and prop.get("canonical_description"):
                prompt_parts.append("道具连续性锚点：" + prop["canonical_description"])
        prompt_parts.extend("道具状态约束：" + x for x in shot.get("prop_state_anchors", []))
        cast_names = [c["name"] for c in matched]
        if matched:
            prompt_parts.append("本镜头只允许出现已指定角色：" + "、".join(cast_names) + "；不得复制人物、不得新增无关人物、不得串用其他角色的脸和服装")
        else:
            prompt_parts.append("本镜头为无人物角色镜头，只绘制明确描述的环境与物品；任何物品铭文、墓碑姓名、对白或旁白中的人名都不是出场人物，画面不得出现人物、人体、面孔、剪影、幽灵或拟人实体")
        if memorial_only:
            prompt_parts.append("以下姓名只作为墓碑或纪念物铭文出现：" + "、".join(c["name"] for c in memorial_only) + "；只允许呈现石碑/物品及文字，绝对不得生成该姓名对应的人物、肖像、鬼魂或人体")
        for c in matched:
            prompt_parts.extend(c["identity_anchors"])
            if c.get("visual_lock"): prompt_parts.append(c["visual_lock"])
            if c.get("continuity_note"): prompt_parts.append(c["continuity_note"])
            if c.get("entity_type") == "animal":
                prompt_parts.append("动物主体，保持真实动物解剖结构，不得拟人化，不得生成任何人类角色")
            elif c.get("entity_type") == "memorial_only":
                prompt_parts.append("仅允许墓碑、姓名或已指定纪念物，不得生成该角色实体")
        negatives = ([shot.get("negative_prompt", "")] +
                     [c["negative_constraints"] for c in matched + memorial_only if c.get("negative_constraints")])
        if not matched or memorial_only:
            negatives.append("人物、人体、脸、女性、男性、女鬼、幽灵、剪影、拟人实体、额外角色、群像")
        if matched:
            negatives.append("重复人物、额外人物、角色身份互换、性别错误、发型漂移、服装颜色错误、角色串脸")
        if any(c.get("entity_type") == "animal" for c in matched):
            negatives.append("人类、女性、男性、人物拼贴、拟人身体、人物面孔")
        jobs.append({
            "shot_id": shot["shot_id"], "scene_key": shot.get("scene_key") or shot.get("scene_id"),
            "scene_anchor": scene.get("canonical_description") if scene else shot.get("scene_anchor", ""),
            "prop_keys": list(shot.get("prop_keys") or []),
            "prop_states": dict(shot.get("prop_states") or {}),
            "prop_state_anchors": list(shot.get("prop_state_anchors") or []),
            "prompt": " ".join(x for x in prompt_parts if x),
            "negative_prompt": " ".join(x for x in negatives if x),
            "duration_seconds": shot["duration_seconds"],
            "character_anchors": [{"id": c["id"], "name": c["name"], "role": c.get("role"),
                "identity_anchors": c["identity_anchors"], "visual_lock": c.get("visual_lock"),
                "continuity_note": c.get("continuity_note"), "scene_scope": c.get("scene_scope"),
                "entity_type": c.get("entity_type"), "reference_status": c.get("reference_status"),
                "voice_profile": c.get("voice_profile")} for c in matched],
            "reference_images": list(dict.fromkeys(p for c in matched for p in c["reference_images"])),
            "identity_policy": "only matched cast references; never borrow another character's reference; preserve identity and costume across same scene",
            "acceptance": ["correct subject and action", "character identity and costume continuity", "no severe anatomy or temporal artifacts", "output file decodes"],
        })
    return {"schema_version": 1, "provider_preference": provider, "jobs": jobs}


def build_generation_manifest(shot_plan: dict) -> dict:
    """Record known provenance; unknown model/provider values stay explicitly null."""
    entries = []
    for shot in shot_plan.get("shots", []):
        entries.append({
            "shot_id": shot["shot_id"],
            "status": shot.get("status") or "planned",
            "provider_id": shot.get("provider_id"),
            "model_id": shot.get("model_id"),
            "workflow_version": shot.get("workflow_version"),
            "seed": shot.get("seed"),
            "width": shot.get("width"), "height": shot.get("height"),
            "duration_seconds": shot.get("duration_seconds"),
            "output_file": shot.get("image_file") or None,
            "sha256": shot.get("sha256"),
            "elapsed_seconds": shot.get("elapsed_seconds"),
            "error": shot.get("error"),
            "provenance_completeness": "partial" if not all(shot.get(k) is not None for k in ("provider_id", "model_id", "seed")) else "recorded",
        })
    return {"schema_version": 1, "pipeline_version": PIPELINE_VERSION,
            "shots": entries, "note": "Unknown historical provenance is left null; it is never guessed."}


def audit_shots(shot_plan: dict, asset_bible: dict, project_files: list[dict] | None = None,
                uploads_dir: str | Path | None = None,
                keyframe_dir: str | Path | None = None) -> dict:
    """Structural QC only. Pixel-level/identity judgments require a vision review."""
    files = project_files or []
    by_name = {key: x for x in files for key in (str(x.get("name", "")), str(x.get("stored", ""))) if key}
    checks = []
    chars = asset_bible.get("characters") or []
    # Audit the effective prompt that will actually be sent to generation:
    # build_generation_brief appends the matched character's canonical anchors.
    effective_prompts = {job["shot_id"]: job.get("prompt", "")
                         for job in build_generation_brief(shot_plan, asset_bible).get("jobs", [])}
    for shot in shot_plan.get("shots", []):
        sid = shot["shot_id"]
        issues = []
        pending = []
        warnings = []
        if not shot["visual"]: issues.append("missing_visual_description")
        if not shot["action"]: issues.append("missing_action")
        if not shot["prompt"]: issues.append("missing_generation_prompt")
        if shot["duration_seconds"] <= 0: issues.append("invalid_duration")
        image_name = _text(shot.get("image_file"))
        if not image_name:
            legacy = shot.get("source_legacy_id")
            image_name = f"shot_{int(legacy):04d}.png" if str(legacy).isdigit() else ""
        image_meta = by_name.get(image_name)
        image_path = None
        if image_meta and uploads_dir:
            stored = _text(image_meta.get("stored"))
            candidate = Path(uploads_dir) / stored if stored else None
            if candidate and candidate.is_file(): image_path = candidate
        if image_path is None and keyframe_dir and image_name:
            candidate = Path(keyframe_dir) / Path(image_name).name
            if candidate.is_file(): image_path = candidate
        image_exists = image_path is not None
        image_dimensions = None
        if not image_exists:
            # A not-yet-generated frame is production work, not a malformed
            # shot. Keep it visible as pending so it does not flood the
            # structural-failure queue or get mislabeled as a regeneration.
            pending.append("awaiting_keyframe_generation")
        else:
            try:
                from PIL import Image
                with Image.open(image_path) as im:
                    image_dimensions = {"width": im.width, "height": im.height, "format": im.format}
                if image_dimensions["width"] < 640 or image_dimensions["height"] < 360:
                    issues.append("keyframe_resolution_too_low")
                if image_dimensions["width"] / image_dimensions["height"] < 1.7:
                    issues.append("keyframe_aspect_ratio_not_16_9")
            except Exception:
                issues.append("keyframe_unreadable")
        review = shot.get("visual_review") if isinstance(shot.get("visual_review"), dict) else {}
        review_status = _text(review.get("status") or "pending")
        prompt = effective_prompts.get(sid, shot["prompt"])
        # Only audit the cast actually assigned to this shot. Checking every
        # character in the episode creates false failures for environment-only
        # shots, animals, and characters who are not present in the scene.
        shot_ids = set(shot.get("character_ids") or [])
        shot_names = set(shot.get("character_names") or [])
        known_ids = {char.get("id") for char in chars}
        known_names = {char.get("name") for char in chars}
        for unknown_id in sorted(shot_ids - known_ids):
            issues.append("unknown_character_id:" + str(unknown_id))
        for unknown_name in sorted(shot_names - known_names):
            issues.append("unknown_character_name:" + str(unknown_name))
        if shot_ids and shot_names:
            id_names = {char.get("name") for char in chars if char.get("id") in shot_ids}
            if id_names and not shot_names.issubset(id_names):
                issues.append("character_id_name_mismatch")
        # Empty cast is intentional. Names appearing in prop text, inscriptions,
        # dialogue, or narration never create a cast assignment.
        matched_chars = [char for char in chars
                         if (shot_ids and char.get("id") in shot_ids)
                         or (shot_names and char.get("name") in shot_names)]
        source_description = " ".join([shot.get("visual", ""), shot.get("action", ""), shot.get("camera", {}).get("movement", "") if isinstance(shot.get("camera"), dict) else ""])
        for char in matched_chars:
            if char.get("entity_type") == "animal" and not any(token in source_description for token in ("乌鸦", "鸟", "羽毛", "动物", "兽")):
                warnings.append("cast_may_not_match_shot_subject:" + char["id"])
            # Memorial-only entities (e.g. a person represented solely by a
            # gravestone in this episode) intentionally have no human reference
            # image. Treating that as a missing character asset creates a false
            # warning and can pressure the workflow to generate a forbidden body.
            if char.get("reference_status") == "missing" and char.get("entity_type") != "memorial_only":
                warnings.append("character_reference_missing:" + char["id"])
            anchors = char.get("identity_anchors") or []
            # Soft warning: at least one canonical identity anchor should be
            # visible in the authored prompt for a cast-bearing shot.
            if anchors and not any(anchor in prompt for anchor in anchors):
                issues.append("character_anchor_not_in_prompt:" + char["id"])
        critical = any(token in (shot.get("visual", "") + shot.get("action", ""))
                       for token in ("首次", "揭示", "显现", "死亡", "转折", "爆发", "变身", "特写关键"))
        verdict = ("needs_attention" if issues or review_status in ("rejected", "needs_revision")
                   else "awaiting_generation" if pending
                   else "sample_review" if critical else "auto_pass_pending_render")
        checks.append({"shot_id": sid, "verdict": verdict,
                       "issues": issues, "pending": pending, "warnings": warnings, "image_file": image_name or None,
                       "file_status": "present" if image_exists else "missing",
                       "image_dimensions": image_dimensions,
                       "visual_review": {"status": review_status, "notes": _text(review.get("notes")), "reviewed_at": review.get("reviewed_at")},
                       "review_policy": "human_sample_review" if critical or issues else "automatic",
                       "visual_review_required": review_status != "approved" or bool(critical or issues)})
    failed = [x for x in checks if x["issues"] or x["visual_review"]["status"] in ("rejected", "needs_revision")]
    awaiting_generation = [x for x in checks if x.get("pending")]
    visual_counts = {state: sum(1 for x in checks if x["visual_review"]["status"] == state)
                     for state in ("pending", "approved", "needs_revision", "rejected")}
    overall_status = ("needs_attention" if failed else
                      "awaiting_generation" if awaiting_generation else
                      "ready_for_visual_review")
    return {"schema_version": 1, "pipeline_version": PIPELINE_VERSION,
            "status": overall_status,
            "checked_at": time.time(), "structural_only": True,
            "summary": {"shot_count": len(checks), "needs_attention": len(failed),
                        "structural_issue_count": sum(1 for x in checks if x["issues"]),
                        "pending_generation_count": len(awaiting_generation),
                        "missing_keyframe_count": sum(1 for x in checks if "awaiting_keyframe_generation" in x.get("pending", [])),
                        "visual_review": visual_counts},
            "shots": checks,
            "automatic_pass_count": sum(1 for x in checks if not x["issues"] and not x.get("pending") and x["verdict"] != "sample_review"),
            "critical_sample_count": sum(1 for x in checks if x["verdict"] == "sample_review"),
            "notice": "Structural checks run automatically. Only critical shots or detected exceptions are routed for human review; visual identity and motion quality are not claimed verified without image/video analysis."}


def route_regeneration(qc_report: dict) -> dict:
    jobs = []
    for shot in qc_report.get("shots", []):
        issues = shot.get("issues") or []
        review_status = (shot.get("visual_review") or {}).get("status")
        if not issues and review_status not in ("rejected", "needs_revision"):
            continue
        action = "regenerate_keyframe" if review_status in ("rejected", "needs_revision") or any(x == "missing_keyframe" or x.startswith("character_anchor") for x in issues) else "revise_shot_brief"
        fix_items = list(issues)
        if review_status in ("rejected", "needs_revision"):
            fix_items.append("visual_review:" + review_status)
        jobs.append({"shot_id": shot["shot_id"], "action": action,
                     "only_fix": fix_items, "preserve_accepted_shots": True,
                     "max_attempts": 2})
    return {"schema_version": 1, "jobs": jobs,
            "policy": "targeted_retry_only; never regenerate accepted shots by default"}


def build_canonical_timeline(project_id: str, title: str, shot_plan: dict,
                             media_by_shot: dict[str, str] | None = None,
                             aspect_ratio: str = "16:9") -> dict:
    media_by_shot = media_by_shot or {}
    if aspect_ratio not in ("16:9", "9:16", "3:4"): aspect_ratio = "16:9"
    width, height = {"16:9": (1920, 1080), "9:16": (1080, 1920), "3:4": (1440, 1920)}[aspect_ratio]
    cursor = 0.0
    clips = []
    for shot in shot_plan.get("shots", []):
        duration = max(0.1, float(shot.get("duration_seconds") or 4.0))
        media = media_by_shot.get(shot["shot_id"])
        clips.append({"item_type": "clip", "clip_id": shot["shot_id"],
                      "scene_id": shot.get("scene_id"), "start_seconds": round(cursor, 3),
                      "duration_seconds": round(duration, 3), "end_seconds": round(cursor+duration, 3),
                      "media_reference": {"path": media} if media else None,
                      "metadata": {"shot_type": shot.get("shot_type"),
                                   "camera": shot.get("camera"), "status": shot.get("status")}})
        cursor += duration
    return {"version": 1, "kind": "canonical_timeline", "schema": "otio-inspired",
            "project_id": project_id, "title": title, "frame_rate": 24,
            "aspect_ratio": aspect_ratio, "resolution": {"width": width, "height": height},
            "duration_seconds": round(cursor, 3),
            "tracks": [{"track_id": "picture", "track_type": "video", "children": clips},
                       {"track_id": "dialogue", "track_type": "audio", "children": []}],
            "transitions": [], "scene_index": []}


def compile_production(production: dict, project: dict | None = None,
                       project_files: list[dict] | None = None,
                       uploads_dir: str | Path | None = None,
                       keyframe_dir: str | Path | None = None) -> dict:
    spec = build_project_spec(production, project)
    bible = build_asset_bible(production, project_files)
    plan = build_shot_plan(production)
    brief = build_generation_brief(plan, bible)
    manifest = build_generation_manifest(plan)
    qc = audit_shots(plan, bible, project_files, uploads_dir, keyframe_dir)
    regen = route_regeneration(qc)
    timeline = build_canonical_timeline(spec["project_id"], spec["title"], plan,
                                        aspect_ratio=spec["format"]["aspect_ratio"])
    return {"project_spec": spec, "asset_bible": bible, "shot_plan": plan,
            "generation_brief": brief, "generation_manifest": manifest,
            "qc_report": qc, "regeneration_brief": regen, "canonical_timeline": timeline}
