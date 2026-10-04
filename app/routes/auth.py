from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.config import get_settings
from app.security import verify_password

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def is_authenticated(request: Request) -> bool:
    return request.session.get("admin_logged_in") is True


def require_auth(request: Request) -> None:
    if not is_authenticated(request):
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"}
        )


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if is_authenticated(request):
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...)
):
    settings = get_settings()
    
    # Check credentials
    username_match = (username.strip() == settings.ADMIN_USERNAME.strip())
    password_match = verify_password(password, settings.ADMIN_PASSWORD_HASH)
    
    if username_match and password_match:
        request.session["admin_logged_in"] = True
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
        
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": "Username atau kata sandi tidak valid.", "username": username},
        status_code=status.HTTP_401_UNAUTHORIZED
    )


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
