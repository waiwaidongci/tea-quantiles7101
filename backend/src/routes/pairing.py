from fastapi import APIRouter, Depends
from src.config.database import get_db
from src.middlewares.auth import admin, current_user, writer
from src.schemas.pairing import (
    PairingCorrectionInput, PairingReviewInput, PairingRunInput, PairingStudyInput,
)
from src.controllers import pairing
router = APIRouter()


@router.get('/pairing/studies')
def list_studies(db=Depends(get_db), user=Depends(current_user)):
    return pairing.overview(db)['studies']


@router.post('/pairing/studies')
def create_study(data: PairingStudyInput, db=Depends(get_db), user=Depends(writer)):
    return pairing.create_study(db, data, user)


@router.get('/pairing/studies/{study_id}/readings')
def readings(study_id: int, db=Depends(get_db), user=Depends(current_user)):
    return pairing.list_readings(db, study_id)


@router.post('/pairing/studies/{study_id}/corrections')
def correct(study_id: int, data: PairingCorrectionInput, db=Depends(get_db), user=Depends(writer)):
    return pairing.correct(db, study_id, data, user)


@router.get('/pairing/runs')
def list_runs(study_id: int | None = None, db=Depends(get_db), user=Depends(current_user)):
    return pairing.overview(db, study_id)['runs']


@router.post('/pairing/studies/{study_id}/runs')
def create_run(study_id: int, data: PairingRunInput, db=Depends(get_db), user=Depends(writer)):
    return pairing.create_run(db, study_id, data, user)


@router.get('/pairing/runs/{run_id}')
def get_run(run_id: int, db=Depends(get_db), user=Depends(current_user)):
    return pairing.get_run(db, run_id)


@router.post('/pairing/runs/{run_id}/review')
def review(run_id: int, data: PairingReviewInput, db=Depends(get_db), user=Depends(admin)):
    return pairing.review_run(db, run_id, data, user)
