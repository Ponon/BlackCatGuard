from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from dao.models import Administrator


router = APIRouter()


@router.get("/")
async def home():
    if not await Administrator.exists():
        return RedirectResponse("/setup", status_code=303)

    return RedirectResponse("/login", status_code=303)