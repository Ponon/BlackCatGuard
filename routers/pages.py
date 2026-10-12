from fastapi import APIRouter
from fastapi.responses import RedirectResponse

from database import has_admin


router = APIRouter()


@router.get("/")
async def home():
    if not await has_admin():
        return RedirectResponse("/setup", status_code=303)

    return RedirectResponse("/login", status_code=303)