from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes.production import router as production_router
from app.routes.hardware import router as hardware_router
from app.routes.history import router as history_router

app = FastAPI(
    title="AI Production and Hardware API",
    description="AI-powered production and hardware analysis",
    version="1.0.0",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(production_router)
app.include_router(hardware_router)
app.include_router(history_router)

@app.get("/")
def root():
    return {
        "message": "AI Production and Hardware API is running"
    }