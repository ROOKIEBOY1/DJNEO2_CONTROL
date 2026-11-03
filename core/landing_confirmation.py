"""Eligibility restricted to the state preceding captured 03/2A=1E.

This is not a terrain-safety classifier. Only an explicit current land request
plus persistent capture-matched telemetry allows one subsequent command.
"""
import math
import time

def eligible_landing_confirmation(status,now=None):
    now=time.time() if now is None else now
    if not all(status.get(k) is True for k in ('land_requested','land_sent','land_acknowledged')):return False
    flight=status.get('landing_observation',{})
    try:
        age=flight['telemetry_age_s'];requested=flight['requested_at'];at=flight['at']
        height=flight['relative_height_candidate_m']
        descent=flight['descending_duration_s']
        return bool(all(math.isfinite(v) for v in (age,requested,at,height,descent))
            and 0<=age<=1 and requested<=at<=now and 0<=now-at<=1 and 3<=now-requested<=20
            and flight.get('mode_raw')==12 and flight.get('last_command_raw')==2
            and flight.get('landing_reason_raw')==0x0d and descent>=3
            and flight.get('motors_on') is True and flight.get('airborne_flag') is True and 0<=height<=.5)
    except (KeyError,TypeError,ValueError):return False
