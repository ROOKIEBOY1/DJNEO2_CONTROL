"""Receive-only overlay geometry. No target selection or flight actions."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
import math

def _fresh(age):
    return isinstance(age,(int,float)) and math.isfinite(age) and 0<=age<=1

def preview_boxes(observation):
    result=dict(boxes=[],lock_verified=False,
                coordinate_interpretation='center-size; inferred from owned captures')
    tracking_age=observation.get('tracking_box_age_s')
    detection_age=observation.get('detection_age_s')
    if (_fresh(detection_age) and observation.get('candidate_count')==0
            and (not _fresh(tracking_age) or detection_age<=tracking_age)):
        return result
    tracks=(observation.get('tracking_box_candidate') or {}).get('targets',[])
    if _fresh(tracking_age) and tracks:
        targets,source=tracks,'tracking'
    elif _fresh(detection_age):
        targets,source=observation.get('candidates',[]),'detection'
    else:return result
    for target in targets[:16]:
        try:
            box=target['bbox'];x,y,w,h=(box[k] for k in ('x','y','width','height'))
            if not all(math.isfinite(v) for v in (x,y,w,h)) or not (0<=x<=1 and 0<=y<=1 and 0<w<=1 and 0<h<=1):continue
            left,top=max(0,x-w/2),max(0,y-h/2)
            right,bottom=min(1,x+w/2),min(1,y+h/2)
            result['boxes'].append(dict(id=int(target['id']),left=round(left,6),top=round(top,6),
                                        width=round(right-left,6),height=round(bottom-top,6),source=source))
        except (KeyError,TypeError,ValueError,OverflowError):continue
    return result
