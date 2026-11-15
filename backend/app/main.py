import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import access, admin, auth, face, health, liveness
from app.core.config import settings
from app.schemas.face_detection import ImageTooLargeError
from app.schemas.liveness import LivenessPayloadTooLargeError
from app.services.face_recognition import (
    FaceRecognitionUnavailableError,
    face_embedding_service,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        face_embedding_service.warm_up()
    except FaceRecognitionUnavailableError as exc:
        logger.warning("Face model warm-up failed; identity checks will fail closed: %s", exc)
    yield

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Backend foundation for the HID face liveness and identity verification system.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(face.router, prefix="/api/face")
app.include_router(liveness.router, prefix="/api/liveness")
app.include_router(auth.router, prefix="/api/auth")
app.include_router(access.router, prefix="/api/access")
app.include_router(admin.router, prefix="/api/admin")


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    errors = exc.errors()
    status_code = (
        413
        if any(
            isinstance(
                (error.get("ctx") or {}).get("error"),
                (ImageTooLargeError, LivenessPayloadTooLargeError),
            )
            for error in errors
        )
        else 422
    )
    safe_errors = [
        {
            "loc": error.get("loc", ()),
            "msg": error.get("msg", "Invalid request."),
            "type": error.get("type", "value_error"),
        }
        for error in errors
    ]
    return JSONResponse(status_code=status_code, content={"detail": safe_errors})


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "error": str(exc)},
    )


@app.get("/")
async def root() -> dict[str, str]:
    return {"message": f"{settings.APP_NAME} API is running."}
