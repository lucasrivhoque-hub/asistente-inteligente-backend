import asyncio
import base64
import json
import os
from dotenv import load_dotenv
load_dotenv()
from datetime import datetime, timedelta

from fastapi import (
    FastAPI, Depends, HTTPException, status, WebSocket, WebSocketDisconnect
)
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, DateTime, Boolean, Float
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, Session
from passlib.context import CryptContext
from jose import JWTError, jwt
from pydantic import BaseModel
from google import genai
from google.genai import types

# --- Conexión a la base de datos ---
DATABASE_URL = os.environ.get("DATABASE_URL")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# --- Seguridad ---
SECRET_KEY = "cambia-esto-por-una-clave-larga-y-secreta-propia"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# --- Gemini Live ---
os.environ["GOOGLE_API_KEY"] = os.environ.get("GEMINI_API_KEY")
gemini_client = genai.Client()
GEMINI_MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"

gemini_config = types.LiveConnectConfig(
    response_modalities=["AUDIO"],
    system_instruction=(
        "Eres un asistente de voz para una persona con discapacidad visual. "
        "Puedes ver a través de una cámara en tiempo real. Describe el entorno, "
        "objetos y posibles obstáculos cuando te lo pidan. Responde de forma breve, "
        "clara y natural en español."
    ),
)

# --- Modelos (tablas) ---
class Rol(Base):
    __tablename__ = "roles"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, unique=True, nullable=False)

class Usuario(Base):
    __tablename__ = "usuarios"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    rol_id = Column(Integer, ForeignKey("roles.id"))
    rol = relationship("Rol")

class Dispositivo(Base):
    __tablename__ = "dispositivos"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, nullable=False)
    identificador_sdk = Column(String, unique=True, nullable=True)
    bateria_pct = Column(Float, nullable=True)
    activo = Column(Boolean, default=True)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"))
    usuario = relationship("Usuario")

class Ruta(Base):
    __tablename__ = "rutas"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String, nullable=False)
    origen = Column(String, nullable=False)
    destino = Column(String, nullable=False)
    fecha_creacion = Column(DateTime, default=datetime.utcnow)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"))
    usuario = relationship("Usuario")

class Alerta(Base):
    __tablename__ = "alertas"
    id = Column(Integer, primary_key=True, index=True)
    tipo = Column(String, nullable=False)
    descripcion = Column(String, nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    usuario_id = Column(Integer, ForeignKey("usuarios.id"))
    dispositivo_id = Column(Integer, ForeignKey("dispositivos.id"))
    usuario = relationship("Usuario")
    dispositivo = relationship("Dispositivo")

Base.metadata.create_all(bind=engine)

# --- Utilidades de sesión de base de datos ---
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- Esquemas de entrada/salida ---
class UsuarioRegistro(BaseModel):
    nombre: str
    email: str
    password: str
    rol_nombre: str

class Token(BaseModel):
    access_token: str
    token_type: str

# --- Funciones de seguridad ---
def crear_token(data: dict):
    to_encode = data.copy()
    expira = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expira})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def usuario_actual(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credenciales_invalidas = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No se pudo validar el token",
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credenciales_invalidas
    except JWTError:
        raise credenciales_invalidas
    usuario = db.query(Usuario).filter(Usuario.email == email).first()
    if usuario is None:
        raise credenciales_invalidas
    return usuario

# --- Estado del video en vivo (último frame recibido) ---
ultimo_frame_b64 = None

# --- API ---
app = FastAPI(title="Asistente Inteligente API")

@app.get("/")
def read_root():
    return {"mensaje": "Backend del Asistente Inteligente funcionando correctamente"}

@app.get("/salud")
def health_check():
    return {"status": "ok"}

@app.post("/usuarios/registro")
def registrar_usuario(datos: UsuarioRegistro, db: Session = Depends(get_db)):
    rol = db.query(Rol).filter(Rol.nombre == datos.rol_nombre).first()
    if not rol:
        rol = Rol(nombre=datos.rol_nombre)
        db.add(rol)
        db.commit()
        db.refresh(rol)

    existente = db.query(Usuario).filter(Usuario.email == datos.email).first()
    if existente:
        raise HTTPException(status_code=400, detail="Ese email ya está registrado")

    nuevo_usuario = Usuario(
        nombre=datos.nombre,
        email=datos.email,
        password_hash=pwd_context.hash(datos.password),
        rol_id=rol.id,
    )
    db.add(nuevo_usuario)
    db.commit()
    db.refresh(nuevo_usuario)
    return {"mensaje": "Usuario creado", "id": nuevo_usuario.id, "rol": rol.nombre}

@app.post("/login", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    usuario = db.query(Usuario).filter(Usuario.email == form_data.username).first()
    if not usuario or not pwd_context.verify(form_data.password, usuario.password_hash):
        raise HTTPException(status_code=401, detail="Email o contraseña incorrectos")
    token = crear_token({"sub": usuario.email})
    return {"access_token": token, "token_type": "bearer"}

@app.get("/usuarios/me")
def leer_usuario_actual(usuario: Usuario = Depends(usuario_actual)):
    return {"id": usuario.id, "nombre": usuario.nombre, "email": usuario.email, "rol": usuario.rol.nombre}

@app.get("/alertas")
def listar_alertas(usuario: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    alertas = db.query(Alerta).order_by(Alerta.timestamp.desc()).limit(50).all()
    return [
        {"id": a.id, "tipo": a.tipo, "descripcion": a.descripcion, "timestamp": a.timestamp.isoformat()}
        for a in alertas
    ]

@app.get("/dispositivos")
def listar_dispositivos(usuario: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    dispositivos = db.query(Dispositivo).all()
    return [{"id": d.id, "nombre": d.nombre, "activo": d.activo} for d in dispositivos]

@app.get("/video/ultimo-frame")
def obtener_ultimo_frame(usuario: Usuario = Depends(usuario_actual)):
    return {"frame": ultimo_frame_b64}

@app.get("/estadisticas")
def obtener_estadisticas(usuario: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    total_alertas = db.query(Alerta).count()
    total_dispositivos = db.query(Dispositivo).count()
    dispositivos_activos = db.query(Dispositivo).filter(Dispositivo.activo == True).count()
    ultima_alerta = db.query(Alerta).order_by(Alerta.timestamp.desc()).first()
    return {
        "total_alertas": total_alertas,
        "total_dispositivos": total_dispositivos,
        "dispositivos_activos": dispositivos_activos,
        "ultima_actividad": ultima_alerta.timestamp.isoformat() if ultima_alerta else None,
    }

# --- WebSocket: sesión en vivo con el asistente de IA ---
@app.websocket("/ws/asistente")
async def websocket_asistente(websocket: WebSocket):
    await websocket.accept()
    db = SessionLocal()

    async def manejar_sesion():
        async with gemini_client.aio.live.connect(model=GEMINI_MODEL, config=gemini_config) as session:

            nueva_alerta = Alerta(
                tipo="sesion",
                descripcion="Sesión de asistente iniciada",
                usuario_id=None,
                dispositivo_id=None,
            )
            db.add(nueva_alerta)
            db.commit()

            async def recibir_del_cliente():
                global ultimo_frame_b64
                while True:
                    mensaje = await websocket.receive_text()
                    datos = json.loads(mensaje)
                    contenido = base64.b64decode(datos["data"])
                    if datos["type"] == "audio":
                        await session.send_realtime_input(
                            audio=types.Blob(data=contenido, mime_type="audio/pcm;rate=16000")
                        )
                    elif datos["type"] == "video":
                        ultimo_frame_b64 = datos["data"]
                        await session.send_realtime_input(
                            video=types.Blob(data=contenido, mime_type="image/jpeg")
                        )

            async def enviar_al_cliente():
                while True:
                    async for respuesta in session.receive():
                        if respuesta.data:
                            audio_b64 = base64.b64encode(respuesta.data).decode("utf-8")
                            await websocket.send_text(json.dumps({"type": "audio", "data": audio_b64}))

            await asyncio.gather(recibir_del_cliente(), enviar_al_cliente())

    try:
        while True:
            try:
                await manejar_sesion()
            except Exception as e:
                print(f"Sesión con Gemini interrumpida, reconectando... ({e})")
                await asyncio.sleep(1)
                continue
    except WebSocketDisconnect:
        print("Cliente desconectado del asistente")
    finally:
        db.close()

# --- Archivos estáticos: interfaz web ---
app.mount("/app", StaticFiles(directory="static", html=True), name="static")