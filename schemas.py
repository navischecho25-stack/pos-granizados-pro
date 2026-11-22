from decimal import Decimal
from pydantic import BaseModel, Field, model_validator

class ShiftOpenRequest(BaseModel):
    business_id: str
    cashier_name: str = Field(..., min_length=2)
    opening_cash: Decimal = Field(default=Decimal("0.00"), ge=0)

class ShiftCloseRequest(BaseModel):
    declared_cash: Decimal = Field(..., ge=0)
    declared_digital: Decimal = Field(..., ge=0)

class SaleItemInput(BaseModel):
    product_id: str
    quantity: int = Field(..., gt=0)

class SaleCreateRequest(BaseModel):
    transaction_uuid: str = Field(..., min_length=10) # Clave de idempotencia
    business_id: str
    shift_id: str
    items: list[SaleItemInput] = Field(..., min_length=1)
    cash_amount: Decimal = Field(default=Decimal("0.00"), ge=0)
    nequi_amount: Decimal = Field(default=Decimal("0.00"), ge=0)
    daviplata_amount: Decimal = Field(default=Decimal("0.00"), ge=0)
    card_amount: Decimal = Field(default=Decimal("0.00"), ge=0)

    @model_validator(mode="after")
    def check_has_payment(self):
        total_paid = self.cash_amount + self.nequi_amount + self.daviplata_amount + self.card_amount
        if total_paid <= 0:
            raise ValueError("El pago total debe ser mayor a cero.")
        return self