import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.database import connect_db, close_db
from app.redis_client import connect_redis, close_redis
from app.routes import health, keys, protected, audit, tenants

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("api_gateway")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting Multi-Tenant API Key Gateway...")
    await connect_db()
    await connect_redis()
    yield
    # Teardown
    logger.info("Shutting down API Gateway...")
    await close_redis()
    await close_db()

app = FastAPI(
    title="Multi-Tenant API Key Gateway & Management Service",
    description="Secure API Gateway with key hashing, rotation grace period, sliding-window rate limiter, and audit logs.",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware for developer integrations and frontend console
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"]
)

# Include API route modules
app.include_router(health.router)
app.include_router(keys.router)
app.include_router(protected.router)
app.include_router(audit.router)
app.include_router(tenants.router)

# Mount static assets for frontend console
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/", include_in_schema=False)
async def serve_console():
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Multi-Tenant API Key Gateway is running. Access /docs for API documentation."}
