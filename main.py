from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from database import engine, Base, get_db
from models import Business, Supply, Product, ProductRecipe, CashShift, Sale, SaleItem
from schemas import ShiftOpenRequest, ShiftCloseRequest, SaleCreateRequest


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(title="POS Granizados Pro - Core API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"error": "Error interno controlado", "detail": str(exc)},
    )


@app.get("/")
async def root():
    return {
        "system": "POS Granizados Pro - Core API",
        "status": "ONLINE",
        "pos_url": "/pos",
        "docs_url": "/docs",
        "health_url": "/health",
        "demo_business_id": "biz-granizados-001",
    }


@app.get("/health")
async def health_check():
    return {"status": "ONLINE", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/pos")
async def serve_pos():
    pos_file = Path(__file__).resolve().parent / "frontend" / "index.html"
    if not pos_file.exists():
        raise HTTPException(
            status_code=404,
            detail="No se encontró frontend/index.html. Crea esa carpeta y archivo primero.",
        )
    return FileResponse(pos_file)


@app.get("/api/menu/{business_id}")
async def get_menu_and_stock(business_id: str, db: AsyncSession = Depends(get_db)):
    products_res = await db.execute(
        select(Product).where(Product.business_id == business_id, Product.is_active == True)
    )
    supplies_res = await db.execute(
        select(Supply).where(Supply.business_id == business_id)
    )
    return {
        "products": products_res.scalars().all(),
        "supplies": supplies_res.scalars().all(),
    }


@app.post("/api/shifts/open")
async def open_shift(payload: ShiftOpenRequest, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(
        select(CashShift).where(CashShift.business_id == payload.business_id, CashShift.status == "OPEN")
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Ya existe un turno de caja abierto en este negocio.")

    shift = CashShift(
        business_id=payload.business_id,
        cashier_name=payload.cashier_name,
        opening_cash=payload.opening_cash,
        expected_cash=payload.opening_cash,
    )
    db.add(shift)
    await db.commit()
    await db.refresh(shift)
    return shift


@app.post("/api/sales")
async def create_sale(payload: SaleCreateRequest, db: AsyncSession = Depends(get_db)):
    # 1. IDEMPOTENCIA: Si el UUID ya fue procesado (doble clic o reintento offline), no duplica
    existing_sale_res = await db.execute(select(Sale).where(Sale.id == payload.transaction_uuid))
    existing_sale = existing_sale_res.scalar_one_or_none()
    if existing_sale:
        return {"message": "Venta ya procesada (Idempotencia activa)", "sale": existing_sale}

    # 2. Verificar turno abierto
    shift_res = await db.execute(select(CashShift).where(CashShift.id == payload.shift_id))
    shift = shift_res.scalar_one_or_none()
    if not shift or shift.status != "OPEN":
        raise HTTPException(status_code=400, detail="El turno de caja está cerrado o no existe.")

    # 3. Verificar negocio
    biz_res = await db.execute(select(Business).where(Business.id == payload.business_id))
    business = biz_res.scalar_one_or_none()
    if not business:
        raise HTTPException(status_code=404, detail="Negocio no encontrado.")

    # 4. Calcular total, armar ítems y descontar insumos por receta
    total_amount = Decimal("0.00")
    sale_items_objs = []

    for item in payload.items:
        prod_res = await db.execute(
            select(Product)
            .options(selectinload(Product.recipes).selectinload(ProductRecipe.supply))
            .where(Product.id == item.product_id, Product.business_id == payload.business_id)
        )
        product = prod_res.scalar_one_or_none()
        if not product:
            raise HTTPException(status_code=404, detail=f"Producto {item.product_id} no encontrado.")

        line_total = product.price * item.quantity
        total_amount += line_total

        sale_items_objs.append(
            SaleItem(
                sale_id=payload.transaction_uuid,
                product_id=product.id,
                product_name=product.name,
                quantity=item.quantity,
                unit_price=product.price,
                line_total=line_total,
            )
        )

        for recipe in product.recipes:
            consumed = recipe.quantity_required * item.quantity
            recipe.supply.current_stock -= consumed

    # 5. Validar que el pago cuadre exacto con la cuenta
    total_paid = payload.cash_amount + payload.nequi_amount + payload.daviplata_amount + payload.card_amount
    if total_paid != total_amount:
        raise HTTPException(
            status_code=400,
            detail=f"Descuadre en el cobro: La cuenta suma ${total_amount} pero se registraron ${total_paid}.",
        )

    # 6. Calcular impuestos según régimen
    if business.tax_regime == "IMPOCONSUMO_8":
        subtotal = (total_amount / Decimal("1.08")).quantize(Decimal("0.01"))
        tax_amount = total_amount - subtotal
    elif business.tax_regime == "IVA_19":
        subtotal = (total_amount / Decimal("1.19")).quantize(Decimal("0.01"))
        tax_amount = total_amount - subtotal
    else:
        subtotal = total_amount
        tax_amount = Decimal("0.00")

    # 7. Obtener consecutivo seguro por negocio
    max_cons_res = await db.execute(
        select(func.coalesce(func.max(Sale.consecutive), 0)).where(Sale.business_id == payload.business_id)
    )
    next_consecutive = max_cons_res.scalar() + 1

    # 8. Guardar venta y actualizar saldos esperados del turno
    new_sale = Sale(
        id=payload.transaction_uuid,
        business_id=payload.business_id,
        shift_id=shift.id,
        consecutive=next_consecutive,
        cash_amount=payload.cash_amount,
        nequi_amount=payload.nequi_amount,
        daviplata_amount=payload.daviplata_amount,
        card_amount=payload.card_amount,
        subtotal=subtotal,
        tax_amount=tax_amount,
        total_amount=total_amount,
        items=sale_items_objs,
    )

    shift.expected_cash += payload.cash_amount
    shift.expected_nequi += payload.nequi_amount
    shift.expected_daviplata += payload.daviplata_amount
    shift.expected_card += payload.card_amount

    db.add(new_sale)
    await db.commit()

    return {
        "status": "SUCCESS",
        "consecutive": next_consecutive,
        "total_amount": total_amount,
        "tax_amount": tax_amount,
        "shift_balances": {
            "expected_cash": shift.expected_cash,
            "expected_nequi": shift.expected_nequi,
            "expected_daviplata": shift.expected_daviplata,
            "expected_card": shift.expected_card,
        },
    }


@app.post("/api/shifts/{shift_id}/close")
async def close_shift(shift_id: str, payload: ShiftCloseRequest, db: AsyncSession = Depends(get_db)):
    shift_res = await db.execute(select(CashShift).where(CashShift.id == shift_id))
    shift = shift_res.scalar_one_or_none()
    if not shift or shift.status != "OPEN":
        raise HTTPException(status_code=400, detail="Turno no encontrado o ya cerrado.")

    expected_digital = shift.expected_nequi + shift.expected_daviplata + shift.expected_card
    total_expected = shift.expected_cash + expected_digital
    total_declared = payload.declared_cash + payload.declared_digital

    shift.declared_cash = payload.declared_cash
    shift.declared_digital = payload.declared_digital
    shift.difference = total_declared - total_expected
    shift.closed_at = datetime.now(timezone.utc)
    shift.status = "CLOSED"

    await db.commit()
    return {
        "status": "CLOSED",
        "expected_cash": shift.expected_cash,
        "declared_cash": shift.declared_cash,
        "expected_digital": expected_digital,
        "declared_digital": shift.declared_digital,
        "difference": shift.difference,
        "audit_note": "CUADRADO PERFECTO" if shift.difference == 0 else f"DESCUADRE DETECTADO: ${shift.difference}",
    }