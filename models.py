import uuid
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import String, Numeric, Boolean, ForeignKey, DateTime, Integer, Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base

def generate_uuid() -> str:
    return str(uuid.uuid4())

class Business(Base):
    __tablename__ = "businesses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    nit: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    tax_regime: Mapped[str] = mapped_column(String(30), default="NO_RESPONSABLE") # NO_RESPONSABLE, IMPOCONSUMO_8, IVA_19
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

class Supply(Base):
    """Insumos físicos reales: Vasos, pitillos, pulpa en ml, leche condensada en g"""
    __tablename__ = "supplies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False) # 'UND', 'ML', 'G'
    current_stock: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    min_stock: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("10.00"))

class Product(Base):
    """Productos en el menú del POS: Granizado Maracuyá 12oz, Adición Tajín, etc."""
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    category: Mapped[str] = mapped_column(String(50), default="Granizados")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    recipes: Mapped[list["ProductRecipe"]] = relationship(back_populates="product", cascade="all, delete-orphan")

class ProductRecipe(Base):
    """Cuántos insumos consume 1 unidad de producto vendido"""
    __tablename__ = "product_recipes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    supply_id: Mapped[str] = mapped_column(ForeignKey("supplies.id"))
    quantity_required: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    product: Mapped["Product"] = relationship(back_populates="recipes")
    supply: Mapped["Supply"] = relationship()

class CashShift(Base):
    """Control estricto de caja por turno"""
    __tablename__ = "cash_shifts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True)
    cashier_name: Mapped[str] = mapped_column(String(80), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="OPEN") # OPEN, CLOSED

    opening_cash: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    expected_cash: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    expected_nequi: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    expected_daviplata: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    expected_card: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))

    declared_cash: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    declared_digital: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    difference: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)

class Sale(Base):
    """Registro inmutable e idempotente de ventas"""
    __tablename__ = "sales"

    id: Mapped[str] = mapped_column(String(36), primary_key=True) # Generado por el cliente (Idempotencia)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id"), index=True)
    shift_id: Mapped[str] = mapped_column(ForeignKey("cash_shifts.id"), index=True)
    consecutive: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    cash_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    nequi_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    daviplata_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    card_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))

    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="COMPLETED") # COMPLETED, VOIDED

    items: Mapped[list["SaleItem"]] = relationship(back_populates="sale", cascade="all, delete-orphan")

class SaleItem(Base):
    __tablename__ = "sale_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    sale_id: Mapped[str] = mapped_column(ForeignKey("sales.id"))
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"))
    product_name: Mapped[str] = mapped_column(String(100), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    line_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    sale: Mapped["Sale"] = relationship(back_populates="items")