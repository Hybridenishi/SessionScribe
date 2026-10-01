"""GET/PUT /campaign — the speaker map and Whisper vocabulary (Settings screen)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from .. import auth, campaign

router = APIRouter(prefix="/campaign", tags=["campaign"])


@router.get("")
def get_campaign(request: Request, _=auth.require_app):
    path = request.app.state.settings.campaign_file
    try:
        return campaign.to_dict(campaign.load(path))
    except campaign.CampaignError as exc:
        raise HTTPException(404, str(exc)) from None


@router.put("")
def put_campaign(body: dict, request: Request, _=auth.require_app):
    try:
        c = campaign.parse(body)
    except campaign.CampaignError as exc:
        raise HTTPException(422, str(exc)) from None
    campaign.save(request.app.state.settings.campaign_file, c)
    return campaign.to_dict(c)
