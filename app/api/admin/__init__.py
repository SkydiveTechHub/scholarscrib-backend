from fastapi import APIRouter

from app.api.admin.admins import router as admins_router
from app.api.admin.announcements import router as announcements_router
from app.api.admin.audit import router as audit_router
from app.api.admin.auth import router as auth_router
from app.api.admin.lessons import router as lessons_router
from app.api.admin.materials import router as materials_router
from app.api.admin.provider import router as provider_router
from app.api.admin.questions import router as questions_router
from app.api.admin.stats import router as stats_router
from app.api.admin.students import router as students_router
from app.api.admin.terms import router as terms_router

router = APIRouter(prefix="/admin/api")
router.include_router(auth_router)
router.include_router(admins_router)
router.include_router(students_router)
router.include_router(questions_router)
router.include_router(materials_router)
router.include_router(terms_router)
router.include_router(provider_router)
router.include_router(lessons_router)
router.include_router(announcements_router)
router.include_router(audit_router)
router.include_router(stats_router)
