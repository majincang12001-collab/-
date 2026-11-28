"""Pure helpers for cast-aware SD prompt construction."""
import re

def shot_cast_names(shot):
    raw = shot.get('character_names') or shot.get('characters') or shot.get('character') or shot.get('character_name') or []
    if isinstance(raw, str):
        return list(dict.fromkeys(x.strip() for x in re.split(r'[、，,；;|]', raw) if x.strip()))
    if isinstance(raw, list):
        return list(dict.fromkeys(str(x).strip() for x in raw if str(x).strip()))
    return []


def character_anchor_text(character):
    """Keep explicit costume/visual locks even when a free-form prompt anchor exists."""
    fields = ('name', 'costume', 'visual_lock', 'appearance', 'age', 'prompt_anchor')
    parts = []
    seen = set()
    for key in fields:
        value = str(character.get(key, '') or '').strip()
        if value and value not in seen:
            parts.append(value)
            seen.add(value)
    return ', '.join(parts)



def camera_guidance(shot):
    """Return framing-only guidance; never replace the shot's subject, action or camera direction."""
    framing = str(shot.get('shot_type', '') or '').strip().lower()
    if any(k in framing for k in ('手部', '微距', 'macro', 'detail')):
        return 'detail framing, focus tightly on the exact object or body part specified by the shot, preserve surrounding context only when requested'
    if any(k in framing for k in ('特写', '近景', 'close-up', 'close up')):
        return 'close framing, emphasize the exact subject specified by the shot, do not introduce additional subjects'
    if any(k in framing for k in ('中景', 'medium')):
        return 'medium framing, keep the specified subjects and their stated actions clearly readable'
    if any(k in framing for k in ('远景', '全景', '全身', 'wide', 'establishing')):
        return 'wide cinematic framing, preserve the specified environment and spatial relationships; do not add unlisted subjects'
    return 'cinematic composition faithful to the specified subject, action and spatial relationships'
