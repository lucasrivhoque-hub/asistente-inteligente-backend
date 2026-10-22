import asyncio
import base64
import json
import math
import os
import time
from dotenv import load_dotenv
load_dotenv()
from datetime import datetime, timedelta

import httpx
from fastapi import (
    FastAPI, Depends, HTTPException, status, WebSocket, WebSocketDisconnect, Response
)
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy import create_engine, Column, Integer, String, ForeignKey, DateTime, Boolean, Float, Text
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
ACCESS_TOKEN_EXPIRE_MINUTES = 720

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# --- Google Maps ---
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY")

# --- Gemini Live ---
os.environ["GOOGLE_API_KEY"] = os.environ.get("GEMINI_API_KEY")
gemini_client = genai.Client()
GEMINI_MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"

HERRAMIENTAS_AVIVA = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="ver_entorno",
            description=(
                "Toma una foto ACTUAL con la cámara de los lentes del usuario. "
                "Llámala cada vez que el usuario pida que describas lo que tiene enfrente "
                "o alrededor, que le avises de obstáculos, que leas un texto o cartel, "
                "o cualquier cosa que requiera ver."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "detalle": {
                        "type": "boolean",
                        "description": (
                            "true si hay que leer texto, carteles, etiquetas, precios, pantallas "
                            "o distinguir detalles pequeños (usa una foto de alta resolución, "
                            "más lenta). false para una vista general rápida del entorno u obstáculos."
                        ),
                    }
                },
            },
        ),
        types.FunctionDeclaration(
            name="obtener_ruta",
            description=(
                "Calcula una ruta A PIE desde la ubicación actual del usuario hasta un destino "
                "y devuelve distancia, tiempo e indicaciones. Llámala cuando el usuario pida "
                "ir o llegar a un lugar, o pregunte cómo llegar."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "destino": {
                        "type": "string",
                        "description": (
                            "Nombre o dirección del destino incluyendo la ciudad, "
                            "por ejemplo 'Plaza 14 de Septiembre, Cochabamba, Bolivia'."
                        ),
                    }
                },
                "required": ["destino"],
            },
        ),
        types.FunctionDeclaration(
            name="siguiente_indicacion",
            description=(
                "Indica qué debe hacer el usuario ahora en la ruta activa, según su posición "
                "actual: cuántos metros faltan para la siguiente indicación y cuál es. "
                "Llámala cuando el usuario pregunte '¿ahora qué hago?', 'siguiente', "
                "'¿voy bien?' o cuánto falta."
            ),
        ),
    ]
)

INSTRUCCION_AVIVA = (
    "Eres AVIVA, un asistente de voz para una persona con discapacidad visual. "
    "Preséntate SOLO al inicio de la conversación. Si ya estás conversando con el usuario, "
    "no vuelvas a saludar ni a presentarte, y continúa siempre desde lo que ya hablaron. "
    "Tus ojos son la cámara de sus lentes, pero solo ves cuando llamas a la herramienta "
    "ver_entorno. Antes de responder CUALQUIER pregunta sobre lo que hay enfrente, alrededor, "
    "obstáculos o textos, llama SIEMPRE a ver_entorno, aunque creas que ya lo sabes por una imagen "
    "anterior. Mientras esperas el resultado di ÚNICAMENTE 'Un momento, miro' y no describas ni "
    "supongas nada. Solo describe lo que veas en la foto que recibas como resultado. "
    "Si te piden leer texto, carteles, etiquetas, precios o pantallas, o ver detalles pequeños, "
    "llama a ver_entorno con detalle=true y pídele que acerque el texto y se quede quieto unos "
    "segundos; para descripciones generales u obstáculos usa detalle=false. Si no logras leer "
    "algo con claridad, dilo y pídele que se acerque en vez de adivinar. "
    "Nunca describas imágenes anteriores como si fueran actuales. Si la herramienta indica un "
    "error, dile al usuario con claridad que no puedes ver en este momento. "
    "Si recibes imágenes automáticas sin haberlas pedido (modo caminata), úsalas solo como "
    "contexto y no hables de ellas a menos que el usuario pregunte o haya un peligro inmediato. "
    "Cuando el usuario pida ir o llegar a un lugar, llama a obtener_ruta con el destino "
    "(agrega la ciudad, por ejemplo 'Cochabamba, Bolivia', si no la mencionó). Luego dile en una "
    "frase la distancia y el tiempo aproximado, y SOLO la primera indicación. Mientras camina, "
    "cuando pregunte qué hacer ahora, si va bien o cuánto falta, llama a siguiente_indicacion. "
    "Usa únicamente lo que devuelvan las herramientas: nunca inventes calles, giros ni distancias. "
    "Es una ruta peatonal basada en mapas y GPS; recuérdale con naturalidad (no en cada mensaje) "
    "que preste atención al cruzar calles y que esto no reemplaza su bastón. "
    "Durante la conversación recibirás mensajes de texto que empiezan con [CONTEXTO DEL SISTEMA]. "
    "Son datos reales del GPS del usuario (su ubicación actual). NO los leas en voz alta ni "
    "los comentes por iniciativa propia: úsalos únicamente cuando el usuario pregunte dónde está, "
    "por su dirección, o cómo llegar a algún lugar. Cuando pregunte dónde está, responde con la "
    "calle o dirección más reciente que recibiste, sin leer las coordenadas. Si todavía no has "
    "recibido ninguna ubicación, di que aún no tienes la ubicación y NUNCA inventes ni adivines una. "
    "Responde de forma breve, clara y natural en español."
)


def crear_config(handle=None, avanzada=True):
    """Configuración de la sesión con Gemini. 'avanzada' agrega transcripción,
    compresión de contexto y reanudación de sesión."""
    opciones = dict(
        response_modalities=["AUDIO"],
        tools=[HERRAMIENTAS_AVIVA],
        system_instruction=INSTRUCCION_AVIVA,
    )
    if avanzada:
        opciones["input_audio_transcription"] = types.AudioTranscriptionConfig()
        opciones["output_audio_transcription"] = types.AudioTranscriptionConfig()
        opciones["context_window_compression"] = types.ContextWindowCompressionConfig(
            sliding_window=types.SlidingWindow()
        )
        opciones["session_resumption"] = types.SessionResumptionConfig(handle=handle)
    return types.LiveConnectConfig(**opciones)


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

class Interaccion(Base):
    """Una consulta a AVIVA: lo que dijo el usuario, lo que respondió y la imagen enviada."""
    __tablename__ = "interacciones"
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    pregunta = Column(Text, nullable=True)
    respuesta = Column(Text, nullable=True)
    imagen_b64 = Column(Text, nullable=True)
    tiene_imagen = Column(Boolean, default=False)
    detalle = Column(Boolean, default=False)

class SolicitudRuta(Base):
    """Cada vez que el usuario pide indicaciones hacia un lugar."""
    __tablename__ = "solicitudes_ruta"
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    origen = Column(Text, nullable=True)
    destino = Column(Text, nullable=False)
    distancia_m = Column(Integer, nullable=True)
    duracion_min = Column(Integer, nullable=True)
    exito = Column(Boolean, default=False)
    resumen = Column(Text, nullable=True)

Base.metadata.create_all(bind=engine)

def guardar_interaccion(pregunta, respuesta, imagen_b64, detalle):
    db = SessionLocal()
    try:
        db.add(Interaccion(
            pregunta=pregunta or None,
            respuesta=respuesta or None,
            imagen_b64=imagen_b64,
            tiene_imagen=imagen_b64 is not None,
            detalle=bool(detalle),
        ))
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"No se pudo guardar la interacción: {e}")
    finally:
        db.close()

def guardar_solicitud_ruta(origen, destino, distancia_m, duracion_min, exito, resumen):
    db = SessionLocal()
    try:
        db.add(SolicitudRuta(
            origen=origen, destino=destino, distancia_m=distancia_m,
            duracion_min=duracion_min, exito=bool(exito), resumen=resumen,
        ))
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"No se pudo guardar la solicitud de ruta: {e}")
    finally:
        db.close()

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

# --- Estado compartido (prototipo de un solo usuario a la vez) ---
ultimo_frame_b64 = None
ultimo_frame_hora = None
ultima_ubicacion = None
ultima_lat = None
ultima_lng = None
punto_geocodificado = None
direccion_geocodificada = None
ruta_activa = {"ruta": None}

# --- Utilidades de mapas ---
def distancia_metros(lat1, lon1, lat2, lon2):
    radio = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radio * math.asin(math.sqrt(a))

async def geocodificar_inverso(lat, lng):
    """Devuelve la dirección (calle) de unas coordenadas, o None si falla."""
    if not GOOGLE_MAPS_API_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=8) as cliente:
            r = await cliente.get(
                "https://maps.googleapis.com/maps/api/geocode/json",
                params={"latlng": f"{lat},{lng}", "language": "es", "key": GOOGLE_MAPS_API_KEY},
            )
            datos = r.json()
        if datos.get("status") == "OK" and datos.get("results"):
            resultados = datos["results"]
            preferido = next(
                (x for x in resultados
                 if "street_address" in x.get("types", []) or "route" in x.get("types", [])),
                resultados[0],
            )
            return preferido["formatted_address"]
        print(f"Geocoding sin resultado: {datos.get('status')} {datos.get('error_message', '')}")
    except Exception as e:
        print(f"Error en geocodificación inversa: {e}")
    return None

async def calcular_ruta_peatonal(lat, lng, destino):
    """Pide a Google Routes una ruta a pie. Devuelve un dict para Gemini (con '_ruta' interna)."""
    if not GOOGLE_MAPS_API_KEY:
        return {"error": "El servicio de mapas no está configurado en el servidor"}
    cuerpo = {
        "origin": {"location": {"latLng": {"latitude": lat, "longitude": lng}}},
        "destination": {"address": destino},
        "travelMode": "WALK",
        "languageCode": "es",
        "units": "METRIC",
    }
    cabeceras = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_MAPS_API_KEY,
        "X-Goog-FieldMask": (
            "routes.duration,routes.distanceMeters,"
            "routes.legs.steps.navigationInstruction,"
            "routes.legs.steps.distanceMeters,"
            "routes.legs.steps.endLocation"
        ),
    }
    try:
        async with httpx.AsyncClient(timeout=10) as cliente:
            r = await cliente.post(
                "https://routes.googleapis.com/directions/v2:computeRoutes",
                headers=cabeceras, json=cuerpo,
            )
            datos = r.json()
    except Exception as e:
        print(f"Error consultando Routes API: {e}")
        return {"error": "No se pudo consultar el servicio de mapas"}

    if r.status_code != 200 or not datos.get("routes"):
        mensaje = ""
        if isinstance(datos, dict):
            mensaje = (datos.get("error") or {}).get("message", "")
        print(f"Routes API sin ruta (HTTP {r.status_code}): {mensaje}")
        return {"error": "No encontré una ruta a pie hacia ese destino"}

    ruta = datos["routes"][0]
    pasos = []
    for tramo in ruta.get("legs", []):
        for paso in tramo.get("steps", []):
            texto = (paso.get("navigationInstruction") or {}).get("instructions")
            fin = (paso.get("endLocation") or {}).get("latLng") or {}
            if texto and "latitude" in fin and "longitude" in fin:
                pasos.append({
                    "texto": texto,
                    "metros": int(paso.get("distanceMeters", 0)),
                    "fin": (fin["latitude"], fin["longitude"]),
                })
    if not pasos:
        return {"error": "La ruta no trae indicaciones paso a paso"}

    segundos = int(str(ruta.get("duration", "0s")).rstrip("s") or 0)
    print(f"Ruta calculada hacia '{destino}': {ruta.get('distanceMeters', 0)} m, {len(pasos)} indicaciones")
    return {
        "destino": destino,
        "distancia_metros": ruta.get("distanceMeters", 0),
        "duracion_minutos": max(1, round(segundos / 60)),
        "total_indicaciones": len(pasos),
        "primera_indicacion": pasos[0]["texto"],
        "metros_de_la_primera_etapa": pasos[0]["metros"],
        "resumen_de_indicaciones": [p["texto"] for p in pasos[:12]],
        "_ruta": {"destino": destino, "pasos": pasos, "indice": 0},
    }

def indicacion_actual():
    """Dice dónde va el usuario en la ruta activa según su última ubicación."""
    ruta = ruta_activa["ruta"]
    if not ruta:
        return {"error": "No hay una ruta activa. Pregúntale a dónde quiere ir."}
    if ultima_lat is None or ultima_lng is None:
        return {"error": "Aún no tengo la ubicación actual del usuario"}

    pasos = ruta["pasos"]
    i = ruta["indice"]
    while i < len(pasos) - 1 and distancia_metros(ultima_lat, ultima_lng, *pasos[i]["fin"]) < 25:
        i += 1
    ruta["indice"] = i

    restante = round(distancia_metros(ultima_lat, ultima_lng, *pasos[i]["fin"]))
    es_ultima = i == len(pasos) - 1
    if es_ultima and restante < 25:
        return {"llego_al_destino": True, "destino": ruta["destino"]}
    return {
        "destino": ruta["destino"],
        "etapa_actual": f"{i + 1} de {len(pasos)}",
        "indicacion_que_ya_cumple": pasos[i]["texto"],
        "metros_hasta_el_siguiente_cambio": restante,
        "siguiente_indicacion": (
            "Has llegado al destino" if es_ultima else pasos[i + 1]["texto"]
        ),
    }

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
    return {"frame": ultimo_frame_b64, "hora": ultimo_frame_hora}

@app.get("/interacciones")
def listar_interacciones(
    solo_imagenes: bool = False, limite: int = 30,
    usuario: Usuario = Depends(usuario_actual), db: Session = Depends(get_db),
):
    consulta = db.query(Interaccion)
    if solo_imagenes:
        consulta = consulta.filter(Interaccion.tiene_imagen == True)
    filas = consulta.order_by(Interaccion.timestamp.desc()).limit(min(limite, 100)).all()
    return [
        {
            "id": f.id,
            "timestamp": f.timestamp.isoformat(),
            "pregunta": f.pregunta,
            "respuesta": f.respuesta,
            "tiene_imagen": bool(f.tiene_imagen),
            "detalle": bool(f.detalle),
        }
        for f in filas
    ]

@app.get("/interacciones/{interaccion_id}/imagen")
def imagen_de_interaccion(
    interaccion_id: int,
    usuario: Usuario = Depends(usuario_actual), db: Session = Depends(get_db),
):
    fila = db.query(Interaccion).filter(Interaccion.id == interaccion_id).first()
    if not fila or not fila.imagen_b64:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")
    return Response(content=base64.b64decode(fila.imagen_b64), media_type="image/jpeg")

@app.get("/rutas/historial")
def historial_rutas(
    limite: int = 50,
    usuario: Usuario = Depends(usuario_actual), db: Session = Depends(get_db),
):
    filas = db.query(SolicitudRuta).order_by(SolicitudRuta.timestamp.desc()).limit(min(limite, 200)).all()
    return [
        {
            "id": f.id,
            "timestamp": f.timestamp.isoformat(),
            "origen": f.origen,
            "destino": f.destino,
            "distancia_m": f.distancia_m,
            "duracion_min": f.duracion_min,
            "exito": bool(f.exito),
            "resumen": f.resumen,
        }
        for f in filas
    ]

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
        "total_consultas": db.query(Interaccion).count(),
        "total_rutas": db.query(SolicitudRuta).count(),
        "ultima_actividad": ultima_alerta.timestamp.isoformat() if ultima_alerta else None,
    }

def texto_contexto_ubicacion(direccion: str) -> str:
    return (
        "[CONTEXTO DEL SISTEMA - no lo leas en voz alta] "
        f"Ubicación actual del usuario según su GPS: {direccion}"
    )

def turno_vacio():
    return {"pregunta": "", "respuesta": "", "imagen": None, "detalle": False}

# --- WebSocket: sesión en vivo con el asistente de IA ---
@app.websocket("/ws/asistente")
async def websocket_asistente(websocket: WebSocket):
    await websocket.accept()
    db = SessionLocal()
    estado = {
        "cliente_activo": True,
        "foto_pendiente": None,
        "direccion_enviada": None,
        "handle": None,                 # para reanudar la conversación con Gemini
        "herramienta_en_curso": False,
        "turno": turno_vacio(),
    }

    # Una sola alerta por conexión del cliente (no una por cada reconexión a Gemini)
    db.add(Alerta(tipo="sesion", descripcion="Sesión de asistente iniciada",
                  usuario_id=None, dispositivo_id=None))
    db.commit()

    async def manejar_sesion(config, reanudando):
        async with gemini_client.aio.live.connect(model=GEMINI_MODEL, config=config) as session:
            print(f"Sesión con Gemini abierta (reanudando conversación: {reanudando})")

            # En una sesión nueva (no reanudada) damos la ubicación conocida
            if ultima_ubicacion and not reanudando:
                await session.send_realtime_input(text=texto_contexto_ubicacion(ultima_ubicacion))

            async def procesar_ubicacion(texto_app, lat, lng):
                global ultima_ubicacion, ultima_lat, ultima_lng
                global punto_geocodificado, direccion_geocodificada

                direccion = None
                if lat is not None and lng is not None:
                    ultima_lat, ultima_lng = float(lat), float(lng)
                    se_movio = (
                        punto_geocodificado is None
                        or distancia_metros(ultima_lat, ultima_lng, *punto_geocodificado) > 25
                    )
                    if se_movio:
                        direccion = await geocodificar_inverso(ultima_lat, ultima_lng)
                        if direccion:
                            punto_geocodificado = (ultima_lat, ultima_lng)
                            direccion_geocodificada = direccion
                    else:
                        direccion = direccion_geocodificada

                direccion = direccion or texto_app
                if not direccion:
                    return
                if ultima_lat is not None:
                    ultima_ubicacion = f"{direccion} (coordenadas {ultima_lat:.5f}, {ultima_lng:.5f})"
                else:
                    ultima_ubicacion = direccion

                if direccion != estado["direccion_enviada"]:
                    estado["direccion_enviada"] = direccion
                    print(f"Ubicación: {ultima_ubicacion}")
                    await session.send_realtime_input(text=texto_contexto_ubicacion(ultima_ubicacion))

            async def recibir_del_cliente():
                global ultimo_frame_b64, ultimo_frame_hora
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
                            ultimo_frame_hora = datetime.utcnow().isoformat()
                            await session.send_realtime_input(
                                video=types.Blob(data=base64.b64decode(dato), mime_type="image/jpeg")
                            )
                        elif tipo in ("foto", "foto_error"):
                            if tipo == "foto":
                                ultimo_frame_b64 = dato
                                ultimo_frame_hora = datetime.utcnow().isoformat()
                            futuro = estado["foto_pendiente"]
                            if futuro is not None and not futuro.done():
                                futuro.set_result((tipo, dato))
                        elif tipo == "location":
                            await procesar_ubicacion(dato, datos.get("lat"), datos.get("lng"))
                except WebSocketDisconnect:
                    estado["cliente_activo"] = False
                    raise

            async def responder_herramienta(llamada, resultado):
                await session.send_tool_response(
                    function_responses=[
                        types.FunctionResponse(id=llamada.id, name=llamada.name, response=resultado)
                    ]
                )

            async def atender_ver_entorno(llamada):
                reloj = asyncio.get_running_loop()
                inicio = reloj.time()
                detalle = bool((llamada.args or {}).get("detalle", False))
                print(f"AVIVA pidió ver_entorno (detalle={detalle}): solicitando foto a la app...")
                futuro = reloj.create_future()
                estado["foto_pendiente"] = futuro
                await websocket.send_text(json.dumps({"type": "capture_request", "detalle": detalle}))
                try:
                    tipo, dato = await asyncio.wait_for(futuro, timeout=25)
                except asyncio.TimeoutError:
                    tipo, dato = "foto_error", "La cámara tardó demasiado en responder"
                finally:
                    estado["foto_pendiente"] = None

                print(f"Respuesta de la app en {reloj.time() - inicio:.1f} s (tipo: {tipo})")

                if tipo == "foto":
                    estado["turno"]["imagen"] = dato
                    estado["turno"]["detalle"] = detalle
                    await session.send_realtime_input(
                        video=types.Blob(data=base64.b64decode(dato), mime_type="image/jpeg")
                    )
                    resultado = {
                        "resultado": (
                            "Esta es la foto ACTUAL que tomaron los lentes para la pregunta del usuario. "
                            "Descríbela ahora, basándote solo en lo que se ve en ella."
                        )
                    }
                else:
                    print(f"No se obtuvo foto: {dato}")
                    resultado = {"error": f"No se pudo tomar la foto: {dato}. Dile al usuario que no puedes ver en este momento."}

                await responder_herramienta(llamada, resultado)

            async def atender_obtener_ruta(llamada):
                destino = str((llamada.args or {}).get("destino", "")).strip()
                print(f"AVIVA pidió obtener_ruta hacia: '{destino}'")
                if not destino:
                    resultado = {"error": "Falta el destino"}
                elif ultima_lat is None or ultima_lng is None:
                    resultado = {"error": "Aún no tengo la ubicación actual del usuario; espera unos segundos"}
                else:
                    resultado = await calcular_ruta_peatonal(ultima_lat, ultima_lng, destino)
                    ruta_interna = resultado.pop("_ruta", None)
                    if ruta_interna:
                        ruta_activa["ruta"] = ruta_interna
                    resumen = None
                    if ruta_interna:
                        resumen = "\n".join(
                            f"{n + 1}. {p['texto']}" for n, p in enumerate(ruta_interna["pasos"])
                        )
                    await asyncio.to_thread(
                        guardar_solicitud_ruta,
                        estado["direccion_enviada"] or ultima_ubicacion,
                        destino,
                        resultado.get("distancia_metros"),
                        resultado.get("duracion_minutos"),
                        "error" not in resultado,
                        resumen,
                    )
                await responder_herramienta(llamada, resultado)

            async def cerrar_turno():
                # Si AVIVA está usando una herramienta, el turno todavía no terminó
                if estado["herramienta_en_curso"]:
                    return
                turno = estado["turno"]
                pregunta = turno["pregunta"].strip()
                respuesta = turno["respuesta"].strip()
                if pregunta or respuesta or turno["imagen"]:
                    await asyncio.to_thread(
                        guardar_interaccion, pregunta, respuesta, turno["imagen"], turno["detalle"]
                    )
                estado["turno"] = turno_vacio()

            async def enviar_al_cliente():
                while True:
                    async for respuesta in session.receive():
                        # Datos para reanudar la conversación si se corta la conexión
                        if respuesta.session_resumption_update:
                            actualizacion = respuesta.session_resumption_update
                            if actualizacion.resumable and actualizacion.new_handle:
                                estado["handle"] = actualizacion.new_handle
                        if respuesta.go_away is not None:
                            print(f"Gemini avisa que cerrará la sesión pronto (tiempo restante: {respuesta.go_away.time_left})")

                        if respuesta.tool_call:
                            estado["herramienta_en_curso"] = True
                            try:
                                for llamada in respuesta.tool_call.function_calls:
                                    if llamada.name == "ver_entorno":
                                        await atender_ver_entorno(llamada)
                                    elif llamada.name == "obtener_ruta":
                                        await atender_obtener_ruta(llamada)
                                    elif llamada.name == "siguiente_indicacion":
                                        print("AVIVA pidió siguiente_indicacion")
                                        await responder_herramienta(llamada, indicacion_actual())
                                    else:
                                        await responder_herramienta(llamada, {"error": "Herramienta desconocida"})
                            finally:
                                estado["herramienta_en_curso"] = False

                        contenido = respuesta.server_content
                        if contenido:
                            if contenido.input_transcription and contenido.input_transcription.text:
                                estado["turno"]["pregunta"] += contenido.input_transcription.text
                            if contenido.output_transcription and contenido.output_transcription.text:
                                estado["turno"]["respuesta"] += contenido.output_transcription.text
                            if contenido.turn_complete:
                                await cerrar_turno()

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

    usar_avanzada = True
    fallos_rapidos = 0
    try:
        while estado["cliente_activo"]:
            inicio_sesion = time.time()
            handle = estado["handle"] if usar_avanzada else None
            try:
                await manejar_sesion(crear_config(handle, usar_avanzada), reanudando=handle is not None)
            except Exception as e:
                if not estado["cliente_activo"]:
                    break
                duracion = time.time() - inicio_sesion
                print(f"Sesión con Gemini interrumpida tras {duracion:.0f} s, reconectando... ({e})")
                if usar_avanzada and duracion < 5:
                    # Falló al conectar: probamos sin el handle y, si se repite, con la configuración básica
                    fallos_rapidos += 1
                    estado["handle"] = None
                    if fallos_rapidos >= 2:
                        print("La configuración avanzada no fue aceptada; usando la configuración básica")
                        usar_avanzada = False
                else:
                    fallos_rapidos = 0
                await asyncio.sleep(1)
    finally:
        db.close()
        print("Cliente desconectado del asistente")

# --- Archivos estáticos: interfaz web ---
app.mount("/app", StaticFiles(directory="static", html=True), name="static")