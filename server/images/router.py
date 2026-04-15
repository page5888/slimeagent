"""Image upload and serve endpoints."""
import uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from server import config
from server.auth.deps import get_current_user
from server.db.engine import get_db

router = APIRouter(prefix="/images", tags=["images"])


@router.post("/upload")
async def upload_image(
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    """Upload a sprite image (PNG/GIF, max 512KB, max 256x256)."""
    if file.content_type not in ("image/png", "image/gif"):
        raise HTTPException(400, "Only PNG and GIF allowed")

    data = await file.read()
    if len(data) > config.MAX_IMAGE_SIZE:
        raise HTTPException(400, f"File too large (max {config.MAX_IMAGE_SIZE // 1024}KB)")

    # Basic dimension check for PNG
    if file.content_type == "image/png" and len(data) > 24:
        import struct
        w, h = struct.unpack(">II", data[16:24])
        if w > config.MAX_IMAGE_DIMENSION or h > config.MAX_IMAGE_DIMENSION:
            raise HTTPException(400, f"Image too large (max {config.MAX_IMAGE_DIMENSION}x{config.MAX_IMAGE_DIMENSION})")

    image_id = str(uuid.uuid4())
    ext = "png" if file.content_type == "image/png" else "gif"
    filename = f"{image_id}.{ext}"

    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filepath = config.UPLOAD_DIR / filename
    filepath.write_bytes(data)

    db = await get_db()
    await db.execute(
        "INSERT INTO images (id, uploader_id, filename, content_type, size_bytes) "
        "VALUES (?, ?, ?, ?, ?)",
        (image_id, user["user_id"], filename, file.content_type, len(data)),
    )
    await db.commit()

    return {"image_id": image_id, "url": f"/images/{image_id}"}


@router.get("/{image_id}")
async def get_image(image_id: str):
    """Serve an uploaded image."""
    db = await get_db()
    row = await db.execute_fetchone(
        "SELECT filename, content_type FROM images WHERE id = ?",
        (image_id,),
    )
    if not row:
        raise HTTPException(404, "Image not found")

    filepath = config.UPLOAD_DIR / row["filename"]
    if not filepath.exists():
        raise HTTPException(404, "Image file missing")

    return FileResponse(filepath, media_type=row["content_type"])
