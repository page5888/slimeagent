"""AI Slime Agent Relay Server."""
import logging
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from server import config
from server.db.engine import init_db, close_db

logging.basicConfig(
    level=logging.DEBUG if config.DEBUG else logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
log = logging.getLogger("server")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting relay server...")
    await init_db()
    log.info(f"Database ready: {config.DB_PATH}")
    yield
    await close_db()
    log.info("Relay server stopped.")


app = FastAPI(
    title="AI Slime Agent Relay",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers.
#
# equipment / marketplace / federation routers archived — see
# archive/server-side/README.md and ADR 2026-04-30-slime-stays-private.md.
# Slime is meant to be private; equipment is for showing off, marketplace
# turns Slime into a platform, federation merges Slimes into a collective.
# All three contradict 共同沉積 mechanism「每隻 Slime 都不同」 and the
# Slime-doesn't-go-outward principle. Code preserved in archive/ for git
# history; routes are not registered here, so the server doesn't expose
# the endpoints anymore.
from server.auth.router import router as auth_router
from server.wallet.router import router as wallet_router
from server.images.router import router as images_router
from server.evolution.router import router as evolution_router

app.include_router(auth_router)
app.include_router(wallet_router)
app.include_router(images_router)
app.include_router(evolution_router)


# Serve web frontend
_public = Path(__file__).parent / "public"


@app.get("/")
async def landing_page():
    return FileResponse(_public / "index.html")


@app.get("/market")
async def market_page():
    return FileResponse(_public / "market.html")


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.1.0"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server.main:app", host=config.HOST, port=config.PORT,
                reload=config.DEBUG)
