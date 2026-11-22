import os
import secrets
from fastapi import FastAPI, Header, HTTPException, Depends, Request
from fastapi.responses import FileResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# 1. Base de datos: PostgreSQL en producción (Render) o SQLite en desarrollo local
RAW_DB_URL = os.getenv("DATABASE_URL", "sqlite:///./pos_granizados.db")
if RAW_DB_URL.startswith("postgres://"):
    RAW_DB_URL = RAW_DB_URL.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if RAW_DB_URL.startswith("sqlite") else {}
engine_kwargs = {"connect_args": connect_args, "pool_pre_ping": True}
if not RAW_DB_URL.startswith("sqlite"):
    engine_kwargs.update({"pool_size": 5, "max_overflow": 0, "pool_recycle": 300})

engine = create_engine(RAW_DB_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 2. Seguridad por PIN de caja (cámbialo en Render mediante variable de entorno POS_ACCESS_PIN)
POS_ACCESS_PIN = os.getenv("POS_ACCESS_PIN", "1234")

def verify_pos_pin(x_pos_pin: str = Header(default="")):
    if not secrets.compare_digest(x_pos_pin, POS_ACCESS_PIN):
        raise HTTPException(status_code=401, detail="PIN de caja inválido o no proporcionado")