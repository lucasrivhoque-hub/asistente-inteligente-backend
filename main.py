import asyncio
import base64
import json
import math
import os
from dotenv import load_dotenv
load_dotenv()
from datetime import datetime, timedelta

import httpx
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

gemini_config = types.LiveConnectConfig(
    response_modalities=["AUDIO"],
    tools=[HERRAMIENTAS_AVIVA],
    system_instruction=(
        "Eres AVIVA, un asistente de voz para una persona con discapacidad visual. "
        "Tus ojos son la cámara de sus lentes, pero solo ves cuando llamas a la herramienta "
        "ver_entorno. Cuando el usuario te pida ver, describir el entorno, detectar obstáculos "
        "o leer algo, SIEMPRE llama primero a ver_entorno para obtener una foto actual y espera "
        "su resultado antes de responder. Mientras esperas puedes decir brevemente 'un momento, miro'. "
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

# --- Estado compartido (prototipo de un solo usuario a la vez) ---
ultimo_frame_b64 = None
ultima_ubicacion = None          # texto con la dirección, para dárselo a Gemini
ultima_lat = None
ultima_lng = None
punto_geocodificado = None       # (lat, lng) de la última consulta a Geocoding
direccion_geocodificada = None
ruta_activa = {"ruta": None}     # ruta que se está siguiendo

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
    # Avanza de etapa cuando el usuario llega (a menos de 25 m) al final de la actual
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
    estado = {"cliente_activo": True, "foto_pendiente": None, "direccion_enviada": None}

    # Una sola alerta por conexión del cliente (no una por cada reconexión a Gemini)
    db.add(Alerta(tipo="sesion", descripcion="Sesión de asistente iniciada",
                  usuario_id=None, dispositivo_id=None))
    db.commit()

    async def manejar_sesion():
        async with gemini_client.aio.live.connect(model=GEMINI_MODEL, config=gemini_config) as session:

            # Si ya conocemos la ubicación (p. ej. tras una reconexión), se la reenviamos
            if ultima_ubicacion:
                await session.send_realtime_input(text=texto_contexto_ubicacion(ultima_ubicacion))

            async def procesar_ubicacion(texto_app, lat, lng):
                global ultima_ubicacion, ultima_lat, ultima_lng
                global punto_geocodificado, direccion_geocodificada

                direccion = None
                if lat is not None and lng is not None:
                    ultima_lat, ultima_lng = float(lat), float(lng)
                    # Solo consultamos Geocoding si el usuario se movió más de 25 m
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

                # Solo avisamos a Gemini cuando la dirección cambia
                if direccion != estado["direccion_enviada"]:
                    estado["direccion_enviada"] = direccion
                    print(f"Ubicación: {ultima_ubicacion}")
                    await session.send_realtime_input(text=texto_contexto_ubicacion(ultima_ubicacion))

            async def recibir_del_cliente():
                global ultimo_frame_b64
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
                            # 'data' es texto plano (NO base64); lat y lng son números
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
                    print("Foto recibida de la app; entregándola a AVIVA")
                    await session.send_realtime_input(
                        video=types.Blob(data=base64.b64decode(dato), mime_type="image/jpeg")
                    )
                    resultado = {"resultado": "Foto actual recibida. Ya puedes verla; descríbela al usuario."}
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
                await responder_herramienta(llamada, resultado)

            async def enviar_al_cliente():
                while True:
                    async for respuesta in session.receive():
                        if respuesta.tool_call:
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