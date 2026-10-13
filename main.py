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

HERRAMIENTA_VER_ENTORNO = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="ver_entorno",
            description=(
                "Toma una foto ACTUAL con la cámara de los lentes del usuario. "
                "Llámala cada vez que el usuario pida que describas lo que tiene enfrente "
                "o alrededor, que le avises de obstáculos, que leas un texto o cartel, "
                "o cualquier cosa que requiera ver."
            ),
        )
    ]
)

gemini_config = types.LiveConnectConfig(
    response_modalities=["AUDIO"],
    tools=[HERRAMIENTA_VER_ENTORNO],
    system_instruction=(
        "Eres AVIVA, un asistente de voz para una persona con discapacidad visual. "
        "Tus ojos son la cámara de sus lentes, pero solo ves cuando llamas a la herramienta "
        "ver_entorno. Cuando el usuario te pida ver, describir el entorno, detectar obstáculos "
        "o leer algo, SIEMPRE llama primero a ver_entorno para obtener una foto actual y espera "
        "su resultado antes de responder. Mientras esperas puedes decir brevemente 'un momento, miro'. "
        "Nunca describas imágenes anteriores como si fueran actuales. Si la herramienta indica un "
        "error, dile al usuario con claridad que no puedes ver en este momento. "
        "Si recibes imágenes automáticas sin haberlas pedido (modo caminata), úsalas solo como "
        "contexto y no hables de ellas a menos que el usuario pregunte o haya un peligro inmediato. "
        "Durante la conversación recibirás mensajes de texto que empiezan con [CONTEXTO DEL SISTEMA]. "
        "Son datos reales del GPS del usuario (su ubicación actual). NO los leas en voz alta ni "
        "los comentes por iniciativa propia: úsalos únicamente cuando el usuario pregunte dónde está, "
        "por su dirección, o cómo llegar a algún lugar. Cuando pregunte dónde está, responde con la "
        "dirección más reciente que recibiste. Si todavía no has recibido ninguna ubicación, di que "
        "aún no tienes la ubicación y NUNCA inventes ni adivines una. "
        "Responde de forma breve, clara y natural en español."
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

class DispositivoCrear(BaseModel):
    nombre: str
    identificador_sdk: str | None = None

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

# --- Estado compartido ---
ultimo_frame_b64 = None
ultima_ubicacion = None

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

@app.get("/usuarios")
def listar_usuarios(usuario: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    if usuario.rol.nombre != "administrador":
        raise HTTPException(status_code=403, detail="No autorizado")
    usuarios = db.query(Usuario).all()
    return [{"id": u.id, "nombre": u.nombre, "email": u.email, "rol": u.rol.nombre} for u in usuarios]

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

@app.post("/dispositivos")
def crear_dispositivo(datos: DispositivoCrear, usuario: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    nuevo = Dispositivo(nombre=datos.nombre, identificador_sdk=datos.identificador_sdk, activo=True)
    db.add(nuevo)
    db.commit()
    db.refresh(nuevo)
    return {"mensaje": "Dispositivo registrado", "id": nuevo.id}

@app.patch("/dispositivos/{dispositivo_id}/estado")
def cambiar_estado_dispositivo(dispositivo_id: int, usuario: Usuario = Depends(usuario_actual), db: Session = Depends(get_db)):
    dispositivo = db.query(Dispositivo).filter(Dispositivo.id == dispositivo_id).first()
    if not dispositivo:
        raise HTTPException(status_code=404, detail="Dispositivo no encontrado")
    dispositivo.activo = not dispositivo.activo
    db.commit()
    return {"id": dispositivo.id, "activo": dispositivo.activo}

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

def texto_contexto_ubicacion(direccion: str) -> str:
    return (
        "[CONTEXTO DEL SISTEMA - no lo leas en voz alta] "
        f"Ubicación actual del usuario según su GPS: {direccion}"
    )

# --- WebSocket: sesión en vivo con el asistente de IA ---
@app.websocket("/ws/asistente")
async def websocket_asistente(websocket: WebSocket):
    await websocket.accept()
    db = SessionLocal()
    estado = {"cliente_activo": True, "foto_pendiente": None}

    # Una sola alerta por conexión del cliente (no una por cada reconexión a Gemini)
    db.add(Alerta(tipo="sesion", descripcion="Sesión de asistente iniciada",
                  usuario_id=None, dispositivo_id=None))
    db.commit()

    async def manejar_sesion():
        async with gemini_client.aio.live.connect(model=GEMINI_MODEL, config=gemini_config) as session:

            # Si ya conocemos la ubicación (p. ej. tras una reconexión), se la reenviamos
            if ultima_ubicacion:
                await session.send_realtime_input(text=texto_contexto_ubicacion(ultima_ubicacion))

            async def recibir_del_cliente():
                global ultimo_frame_b64, ultima_ubicacion
                try:
                    while True:
                        mensaje = await websocket.receive_text()
                        datos = json.loads(mensaje)
                        tipo = datos.get("type")
                        dato = datos.get("data", "")

                        if tipo == "audio":
                            await session.send_realtime_input(
                                audio=types.Blob(data=base64.b64decode(dato), mime_type="audio/pcm;rate=16000")
                            )
                        elif tipo == "video":
                            # Imagen automática (modo caminata)
                            ultimo_frame_b64 = dato
                            await session.send_realtime_input(
                                video=types.Blob(data=base64.b64decode(dato), mime_type="image/jpeg")
                            )
                        elif tipo in ("foto", "foto_error"):
                            # Respuesta a una solicitud de foto hecha por la herramienta ver_entorno
                            if tipo == "foto":
                                ultimo_frame_b64 = dato
                            futuro = estado["foto_pendiente"]
                            if futuro is not None and not futuro.done():
                                futuro.set_result((tipo, dato))
                        elif tipo == "location":
                            # La ubicación llega como texto plano (NO es base64)
                            ultima_ubicacion = dato
                            print(f"Ubicación recibida: {dato}")
                            await session.send_realtime_input(text=texto_contexto_ubicacion(dato))
                except WebSocketDisconnect:
                    estado["cliente_activo"] = False
                    raise

            async def atender_ver_entorno(llamada):
                print("AVIVA pidió la herramienta ver_entorno: solicitando foto a la app...")
                futuro = asyncio.get_running_loop().create_future()
                estado["foto_pendiente"] = futuro
                await websocket.send_text(json.dumps({"type": "capture_request"}))
                try:
                    tipo, dato = await asyncio.wait_for(futuro, timeout=8)
                except asyncio.TimeoutError:
                    tipo, dato = "foto_error", "La cámara tardó demasiado en responder"
                finally:
                    estado["foto_pendiente"] = None

                if tipo == "foto":
                    print("Foto recibida de la app; entregándola a AVIVA")
                    await session.send_realtime_input(
                        video=types.Blob(data=base64.b64decode(dato), mime_type="image/jpeg")
                    )
                    resultado = {"resultado": "Foto actual recibida. Ya puedes verla; descríbela al usuario."}
                else:
                    print(f"No se obtuvo foto: {dato}")
                    resultado = {"error": f"No se pudo tomar la foto: {dato}. Dile al usuario que no puedes ver en este momento."}

                await session.send_tool_response(
                    function_responses=[
                        types.FunctionResponse(id=llamada.id, name=llamada.name, response=resultado)
                    ]
                )

            async def enviar_al_cliente():
                while True:
                    async for respuesta in session.receive():
                        if respuesta.tool_call:
                            for llamada in respuesta.tool_call.function_calls:
                                if llamada.name == "ver_entorno":
                                    await atender_ver_entorno(llamada)
                                else:
                                    await session.send_tool_response(
                                        function_responses=[
                                            types.FunctionResponse(
                                                id=llamada.id, name=llamada.name,
                                                response={"error": "Herramienta desconocida"},
                                            )
                                        ]
                                    )
                        if respuesta.data:
                            audio_b64 = base64.b64encode(respuesta.data).decode("utf-8")
                            await websocket.send_text(json.dumps({"type": "audio", "data": audio_b64}))

            tareas = [
                asyncio.create_task(recibir_del_cliente()),
                asyncio.create_task(enviar_al_cliente()),
            ]
            hechas, pendientes = await asyncio.wait(tareas, return_when=asyncio.FIRST_EXCEPTION)
            for t in pendientes:
                t.cancel()
            for t in hechas:
                if t.exception():
                    raise t.exception()

    try:
        while estado["cliente_activo"]:
            try:
                await manejar_sesion()
            except Exception as e:
                if not estado["cliente_activo"]:
                    break
                print(f"Sesión con Gemini interrumpida, reconectando... ({e})")
                await asyncio.sleep(1)
    finally:
        db.close()
        print("Cliente desconectado del asistente")

# --- Archivos estáticos: interfaz web ---
app.mount("/app", StaticFiles(directory="static", html=True), name="static")