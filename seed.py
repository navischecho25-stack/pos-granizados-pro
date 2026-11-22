import asyncio
from decimal import Decimal
from database import engine, Base, AsyncSessionLocal
from models import Business, Supply, Product, ProductRecipe

async def seed_data():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as db:
        biz = Business(
            id="biz-granizados-001",
            name="Granizados El Sinú - Montería",
            nit="1067890123-4",
            tax_regime="NO_RESPONSABLE"
        )
        db.add(biz)

        # Insumos medibles
        vaso_12 = Supply(business_id=biz.id, name="Vaso Plástico 12 oz", unit="UND", current_stock=Decimal("300"))
        vaso_16 = Supply(business_id=biz.id, name="Vaso Plástico 16 oz", unit="UND", current_stock=Decimal("200"))
        pitillo = Supply(business_id=biz.id, name="Pitillo Grueso", unit="UND", current_stock=Decimal("500"))
        pulpa_maracuya = Supply(business_id=biz.id, name="Pulpa de Maracuyá", unit="ML", current_stock=Decimal("10000"))
        pulpa_mango = Supply(business_id=biz.id, name="Pulpa de Mango Biche", unit="ML", current_stock=Decimal("10000"))
        leche_cond = Supply(business_id=biz.id, name="Leche Condensada", unit="G", current_stock=Decimal("4000"))

        db.add_all([vaso_12, vaso_16, pitillo, pulpa_maracuya, pulpa_mango, leche_cond])
        await db.flush()

        # Productos del Menú
        p1 = Product(business_id=biz.id, name="Granizado Maracuyá 12 oz", price=Decimal("8000"), category="Granizados")
        p2 = Product(business_id=biz.id, name="Granizado Mango Biche 16 oz", price=Decimal("10000"), category="Granizados")
        p3 = Product(business_id=biz.id, name="Adición Leche Condensada", price=Decimal("1500"), category="Adiciones")
        db.add_all([p1, p2, p3])
        await db.flush()

        # Recetas automáticas
        db.add_all([
            ProductRecipe(product_id=p1.id, supply_id=vaso_12.id, quantity_required=Decimal("1")),
            ProductRecipe(product_id=p1.id, supply_id=pitillo.id, quantity_required=Decimal("1")),
            ProductRecipe(product_id=p1.id, supply_id=pulpa_maracuya.id, quantity_required=Decimal("150")),

            ProductRecipe(product_id=p2.id, supply_id=vaso_16.id, quantity_required=Decimal("1")),
            ProductRecipe(product_id=p2.id, supply_id=pitillo.id, quantity_required=Decimal("1")),
            ProductRecipe(product_id=p2.id, supply_id=pulpa_mango.id, quantity_required=Decimal("200")),

            ProductRecipe(product_id=p3.id, supply_id=leche_cond.id, quantity_required=Decimal("30")),
        ])

        await db.commit()
        print("Base de datos inicializada con éxito. Business ID: biz-granizados-001")

if __name__ == "__main__":
    asyncio.run(seed_data())