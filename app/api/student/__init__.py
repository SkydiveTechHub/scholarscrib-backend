from fastapi import APIRouter

from app.api.student.achievements import router as achievements_router
from app.api.student.announcements import router as announcements_router
from app.api.student.assessments import router as assessments_router
from app.api.student.auth import router as auth_router
from app.api.student.billing import router as billing_router
from app.api.student.classroom import router as classroom_router
from app.api.student.dashboard import router as dashboard_router
from app.api.student.flashcards import router as flashcards_router
from app.api.student.learning_path import router as learning_path_router
from app.api.student.lessons import router as lessons_router
from app.api.student.library import router as library_router
from app.api.student.performance import router as performance_router
from app.api.student.push import router as push_router
from app.api.student.questions import router as questions_router
from app.api.student.study_plan import router as study_plan_router
from app.api.student.subjects import router as subjects_router
from app.api.student.user import router as user_router

router = APIRouter(prefix="/api")
router.include_router(auth_router)
router.include_router(user_router)
router.include_router(subjects_router)
router.include_router(questions_router)
router.include_router(assessments_router)
router.include_router(flashcards_router)
router.include_router(study_plan_router)
router.include_router(lessons_router)
router.include_router(library_router)
router.include_router(achievements_router)
router.include_router(learning_path_router)
router.include_router(announcements_router)
router.include_router(dashboard_router)
router.include_router(performance_router)
router.include_router(classroom_router)
router.include_router(billing_router)
router.include_router(push_router)
