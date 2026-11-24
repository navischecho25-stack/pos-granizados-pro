import os
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    create_engine,
    func,
    text,
)
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker

# ==============================================================================
# 1. CONFIGURACIÓN DE ENTORNO, BASE DE DATOS RESILIENTE Y SEGURIDAD
# ==============================================================================

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_PATH = BASE_DIR / "frontend" / "index.html"

RAW_DB_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'pos_granizados.db'}")
if RAW_DB_URL.startswith("postgres://"):
    RAW_DB_URL = RAW_DB_URL.replace("postgres://", "postgresql://", 1)

IS_SQLITE = RAW_DB_URL.startswith("sqlite")
connect_args = {"check_same_thread": False} if IS_SQLITE else {}

engine_kwargs: Dict[str, Any] = {
    "connect_args": connect_args,
    "pool_pre_ping": True,
    "pool_recycle": 240,
}
if not IS_SQLITE:
    engine_kwargs.update({
        "pool_size": 5,
        "max_overflow": 2,
        "pool_timeout": 30,
    })

engine = create_engine(RAW_DB_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

POS_ACCESS_PIN = os.getenv("POS_ACCESS_PIN", "1234")
MAX_FAILED_PIN_ATTEMPTS = int(os.getenv("MAX_FAILED_PIN_ATTEMPTS", "5"))
PIN_WINDOW_SECONDS = int(os.getenv("PIN_WINDOW_SECONDS", "60"))

_failed_pin_attempts: Dict[str, deque] = defaultdict(deque)


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_pin_rate_limit(client_ip: str) -> None:
    now = time.monotonic()
    window = _failed_pin_attempts[client_ip]
    while window and (now - window[0]) > PIN_WINDOW_SECONDS:
        window.popleft()
    if len(window) >= MAX_FAILED_PIN_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiados intentos fallidos de PIN. Espera un minuto e intenta de nuevo.",
        )


def _record_failed_pin(client_ip: str) -> None:
    now = time.monotonic()
    window = _failed_pin_attempts[client_ip]
    while window and (now - window[0]) > PIN_WINDOW_SECONDS:
        window.popleft()
    window.append(now)


def verify_pos_pin(
    request: Request,
    x_pos_pin: str = Header(default="", alias="X-POS-PIN"),
) -> None:
    client_ip = _get_client_ip(request)
    _check_pin_rate_limit(client_ip)
    if not x_pos_pin or not secrets.compare_digest(x_pos_pin.strip(), POS_ACCESS_PIN):
        _record_failed_pin(client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="PIN de caja inválido o no proporcionado.",
        )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ==============================================================================
# 2. MODELOS DE BASE DE DATOS (TURNO, INSUMO, PRODUCTO, VENTA)
# ==============================================================================

class Shift(Base):
    __tablename__ = "shifts"

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(String(64), nullable=False, index=True)
    opening_cash = Column(Float, nullable=False, default=0.0)
    closing_cash = Column(Float, nullable=True)
    is_open = Column(Boolean, nullable=False, default=True)
    opened_at = Column(DateTime(timezone=True), server_default=func.now())
    closed_at = Column(DateTime(timezone=True), nullable=True)

    sales = relationship("Sale", back_populates="shift")


class Ingredient(Base):
    __tablename__ = "ingredients"

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(String(64), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    unit = Column(String(32), nullable=False, default="unidad")
    stock_qty = Column(Float, nullable=False, default=0.0)


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(String(64), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    price = Column(Float, nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    recipe_ingredient_id = Column(Integer, ForeignKey("ingredients.id"), nullable=True)
    recipe_qty_per_unit = Column(Float, nullable=False, default=1.0)


class Sale(Base):
    __tablename__ = "sales"

    id = Column(Integer, primary_key=True, index=True)
    business_id = Column(String(64), nullable=False, index=True)
    shift_id = Column(Integer, ForeignKey("shifts.id"), nullable=False)
    client_sync_id = Column(String(100), nullable=True, unique=True, index=True)
    total_amount = Column(Float, nullable=False)
    cash_amount = Column(Float, nullable=False, default=0.0)
    nequi_amount = Column(Float, nullable=False, default=0.0)
    daviplata_amount = Column(Float, nullable=False, default=0.0)
    card_amount = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    shift = relationship("Shift", back_populates="sales")


def seed_initial_data(db: Session) -> None:
    business_id = "biz-granizados-001"
    if db.query(Product).filter(Product.business_id == business_id).count() > 0:
        return

    hielo = Ingredient(business_id=business_id, name="Hielo triturado", unit="vaso", stock_qty=500.0)
    jarabe = Ingredient(business_id=business_id, name="Jarabe frutal", unit="porcion", stock_qty=500.0)
    db.add_all([hielo, jarabe])
    db.flush()

    db.add_all([
        Product(
            business_id=business_id,
            name="Granizado Tradicional",
            price=5000.0,
            active=True,
            recipe_ingredient_id=hielo.id,
            recipe_qty_per_unit=1.0,
        ),
        Product(
            business_id=business_id,
            name="Granizado Especial",
            price=8000.0,
            active=True,
            recipe_ingredient_id=jarabe.id,
            recipe_qty_per_unit=1.0,
        ),
    ])
    db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_initial_data(db)
    finally:
        db.close()
    yield


# ==============================================================================
# 3. APLICACIÓN FASTAPI, MIDDLEWARES Y MANEJO GLOBAL DE ERRORES
# ==============================================================================

app = FastAPI(title="POS Granizados El Sinú", version="1.0.0", lifespan=lifespan)

allowed_origins_raw = os.getenv("ALLOWED_ORIGINS", "*")
allowed_origins = (
    ["*"]
    if allowed_origins_raw.strip() == "*"
    else [origin.strip() for origin in allowed_origins_raw.split(",") if origin.strip()]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=allowed_origins != ["*"],
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-POS-PIN"],
)


@app.middleware("http")
async def security_and_logging_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    if request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Error interno controlado del servidor."},
    )


# ==============================================================================
# 4. ESQUEMAS PYDANTIC Y RUTAS (UI, SALUD Y API PROTEGIDA POR PIN)
# ==============================================================================

class ShiftOpenIn(BaseModel):
    business_id: str = Field(default="biz-granizados-001", min_length=1, max_length=64)
    opening_cash: float = Field(ge=0)


class ShiftCloseIn(BaseModel):
    closing_cash: float = Field(ge=0)


class SaleItemIn(BaseModel):
    product_id: int = Field(gt=0)
    quantity: int = Field(gt=0, le=500)


class SaleCreateIn(BaseModel):
    business_id: str = Field(default="biz-granizados-001", min_length=1, max_length=64)
    shift_id: int = Field(gt=0)
    client_sync_id: Optional[str] = Field(default=None, max_length=100)
    cash_amount: float = Field(default=0.0, ge=0)
    nequi_amount: float = Field(default=0.0, ge=0)
    daviplata_amount: float = Field(default=0.0, ge=0)
    card_amount: float = Field(default=0.0, ge=0)
    items: List[SaleItemIn] = Field(min_length=1)


@app.get("/", include_in_schema=False)
@app.get("/pos", include_in_schema=False)
def serve_pos_ui():
    if not FRONTEND_PATH.exists():
        raise HTTPException(status_code=404, detail="Interfaz frontend/index.html no encontrada.")
    return FileResponse(FRONTEND_PATH)


@app.get("/health", tags=["Salud"])
def health_check():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "connected"}
    except Exception:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unhealthy", "database": "disconnected"},
        )


@app.get("/api/products", dependencies=[Depends(verify_pos_pin)], tags=["POS"])
def list_products(business_id: str = "biz-granizados-001", db: Session = Depends(get_db)):
    rows = db.query(Product).filter(Product.business_id == business_id, Product.active.is_(True)).all()
    return [
        {"id": p.id, "name": p.name, "price": p.price}
        for p in rows
    ]


@app.post(
    "/api/shifts/open",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_pos_pin)],
    tags=["Turnos"],
)
def open_shift(payload: ShiftOpenIn, db: Session = Depends(get_db)):
    existing = (
        db.query(Shift)
        .filter(Shift.business_id == payload.business_id, Shift.is_open.is_(True))
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Ya existe un turno abierto para este negocio.")

    shift = Shift(business_id=payload.business_id, opening_cash=payload.opening_cash, is_open=True)
    db.add(shift)
    db.commit()
    db.refresh(shift)
    return {"id": shift.id, "opening_cash": shift.opening_cash, "is_open": shift.is_open}


@app.post(
    "/api/shifts/{shift_id}/close",
    dependencies=[Depends(verify_pos_pin)],
    tags=["Turnos"],
)
def close_shift(shift_id: int, payload: ShiftCloseIn, db: Session = Depends(get_db)):
    shift = db.query(Shift).filter(Shift.id == shift_id).first()
    if not shift:
        raise HTTPException(status_code=404, detail="Turno no encontrado.")
    if not shift.is_open:
        raise HTTPException(status_code=409, detail="El turno ya está cerrado.")

    shift.closing_cash = payload.closing_cash
    shift.is_open = False
    shift.closed_at = func.now()
    db.commit()
    db.refresh(shift)
    return {"id": shift.id, "closing_cash": shift.closing_cash, "is_open": shift.is_open}


@app.post(
    "/api/sales",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_pos_pin)],
    tags=["Ventas"],
)
def create_sale(payload: SaleCreateIn, db: Session = Depends(get_db)):
    if payload.client_sync_id:
        existing_sale = db.query(Sale).filter(Sale.client_sync_id == payload.client_sync_id).first()
        if existing_sale:
            return {"id": existing_sale.id, "total_amount": existing_sale.total_amount, "duplicate": True}

    shift = db.query(Shift).filter(Shift.id == payload.shift_id, Shift.is_open.is_(True)).first()
    if not shift:
        raise HTTPException(status_code=400, detail="No hay un turno abierto válido para registrar la venta.")

    total_items = 0.0
    for item in payload.items:
        product = (
            db.query(Product)
            .filter(Product.id == item.product_id, Product.business_id == payload.business_id, Product.active.is_(True))
            .first()
        )
        if not product:
            raise HTTPException(status_code=404, detail=f"Producto {item.product_id} no encontrado.")

        total_items += product.price * item.quantity

        if product.recipe_ingredient_id:
            ingredient = db.query(Ingredient).filter(Ingredient.id == product.recipe_ingredient_id).first()
            if ingredient:
                needed = product.recipe_qty_per_unit * item.quantity
                if ingredient.stock_qty < needed:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Inventario insuficiente de {ingredient.name}.",
                    )
                ingredient.stock_qty = round(ingredient.stock_qty - needed, 4)

    paid_total = round(
        payload.cash_amount + payload.nequi_amount + payload.daviplata_amount + payload.card_amount,
        2,
    )
    if round(total_items, 2) != paid_total:
        raise HTTPException(
            status_code=400,
            detail=f"El total pagado ({paid_total}) no coincide con el total de la venta ({round(total_items, 2)}).",
        )

    sale = Sale(
        business_id=payload.business_id,
        shift_id=shift.id,
        client_sync_id=payload.client_sync_id,
        total_amount=round(total_items, 2),
        cash_amount=payload.cash_amount,
        nequi_amount=payload.nequi_amount,
        daviplata_amount=payload.daviplata_amount,
        card_amount=payload.card_amount,
    )
    db.add(sale)
    db.commit()
    db.refresh(sale)
    return {"id": sale.id, "total_amount": sale.total_amount, "duplicate": False}