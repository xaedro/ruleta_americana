# main.py
"""
import asyncio
import json
import logging
import os
from typing import cast, Dict, List, Optional
import uuid  # Importamos uuid para generar IDs únicos

from fastapi import (FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel

app = FastAPI()

# --- MIDDLEWARE DE CORS (Configuración flexible para Render y local) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Permite todos los orígenes
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

# --- CONFIGURACIÓN Y ESTADO GLOBAL ---
SECRET_KEY = "tu_clave_secreta_super_dificil"
STREAMER_USERNAME = "mario"
streamer_ws: Optional[WebSocket] = None
streamer_is_active = False

# --- MODELOS PYDANTIC (Sin cambios) ---
class Event(BaseModel): content: str
class NumeroCaido(BaseModel): numero: int
class ConsecutiveData(BaseModel): consecutive_id: int
class DateTimeData(BaseModel): fecha_hora_str: str

# --- CONNECTION MANAGER (Modificado para WebRTC) ---
class ConnectionManager:
    def __init__(self):
        # Usamos un diccionario para mapear ID -> WebSocket
        self.active_connections: Dict[str, WebSocket] = {}
        self.streamer_id: Optional[str] = None

    async def connect(self, websocket: WebSocket) -> str:
        await websocket.accept()
        client_id = str(uuid.uuid4()) # Generamos un ID único para el cliente
        self.active_connections[client_id] = websocket
        print(f"Nuevo cliente conectado con ID: {client_id}")
        return client_id

    def disconnect(self, client_id: str):
        global streamer_ws, streamer_is_active
        if client_id in self.active_connections:
            del self.active_connections[client_id]
        
        if client_id == self.streamer_id:
            print("El streamer se ha desconectado.")
            streamer_ws = None
            streamer_is_active = False
            self.streamer_id = None
            # Notificamos a todos que el stream terminó
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.broadcast_json({"type": "stream_ended"}))

    async def broadcast_json(self, data: dict):
        message = json.dumps(data)
        for client_id, connection in list(self.active_connections.items()):
            try:
                await connection.send_text(message)
            except:
                self.disconnect(client_id)
    
    async def send_personal_json(self, data: dict, client_id: str):
        if client_id in self.active_connections:
            try:
                await self.active_connections[client_id].send_text(json.dumps(data))
            except:
                self.disconnect(client_id)

manager = ConnectionManager()

# --- ENDPOINTS HTTP ---
# Servir la versión antigua (renombrada a main2.html)
@app.get("/old")
async def get_old_version():
    return FileResponse(os.path.join(os.path.dirname(__file__), "index3.html"))

# Servir la nueva versión WebRTC (index4.html)
@app.get("/")
async def get_new_version():
    return FileResponse(os.path.join(os.path.dirname(__file__), "index4.html"))

# Tus otros endpoints HTTP no necesitan cambios
@app.post("/stream_ended")
async def notify_stream_ended(x_secret_key: Optional[str] = Header(None)):
    if x_secret_key != SECRET_KEY:
        raise HTTPException(status_code=403, detail="Clave secreta inválida")
    if manager.streamer_id:
        manager.disconnect(manager.streamer_id)
    return {"status": "ok"}

# ... (tus otros endpoints /send, /consecutivo_juego, etc. no cambian)

# --- ENDPOINT WEBSOCKET (Simplificado y adaptado para WebRTC) ---
@app.websocket("/ws/users")
async def websocket_users(websocket: WebSocket):
    global streamer_ws, streamer_is_active
    client_id = await manager.connect(websocket)
    
    try:
        while True:
            data_str = await websocket.receive_text()
            data = json.loads(data_str)
            msg_type = data.get("type")

        # --- MANEJO DEL HEARTBEAT ---
            if msg_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue # No proceses ni reenvíes este mensaje
            # --- LÓGICA DE SEÑALIZACIÓN WEBRTC ---
            elif msg_type == "offer":
                # Un espectador envía una oferta al streamer
                if manager.streamer_id:
                    print(f"Reenviando oferta de {client_id} a streamer {manager.streamer_id}")
                    # Añadimos el ID del remitente para que el streamer sepa a quién responder
                    data['from_id'] = client_id
                    await manager.send_personal_json(data, manager.streamer_id)

            elif msg_type == "answer":
                # El streamer envía una respuesta a un espectador específico
                target_id = data.get("target_id")
                if target_id:
                    print(f"Reenviando respuesta de streamer a {target_id}")
                    await manager.send_personal_json(data, target_id)

            elif msg_type == "candidate":
                # Un peer envía un candidato al otro
                target_id = data.get("target_id")
                if target_id:
                    # Añadimos el ID del remitente
                    data['from_id'] = client_id
                    await manager.send_personal_json(data, target_id)

            # --- LÓGICA DE LA APLICACIÓN ---
            elif msg_type == "login":
                username = data.get("name", "").lower()
                if username == STREAMER_USERNAME and not manager.streamer_id:
                    manager.streamer_id = client_id
                    streamer_ws = websocket # Mantenemos referencia por compatibilidad si es necesario
                    await manager.send_personal_json({"type": "login_success", "role": "streamer"}, client_id)
                    print(f"Usuario '{username}' ({client_id}) es ahora el Streamer.")
                else:
                    await manager.send_personal_json({"type": "login_success", "role": "viewer"}, client_id)
                    if streamer_is_active:
                        await manager.send_personal_json({"type": "stream_started"}, client_id)

            elif msg_type == "start_stream":
                if client_id == manager.streamer_id:
                    streamer_is_active = True
                    await manager.broadcast_json({"type": "stream_started"})
                    print("El streamer ha iniciado la transmisión.")
            
            elif msg_type == "stream_ended":
                if client_id == manager.streamer_id:
                    manager.disconnect(client_id) # Esto notificará a todos

    except WebSocketDisconnect:
        print(f"Cliente {client_id} desconectado.")
        manager.disconnect(client_id)
    except (ValueError, json.JSONDecodeError):
        print(f"Error de formato de mensaje del cliente {client_id}")
"""

"""
import asyncio
import json
import logging
import os
from typing import cast, Dict, List, Optional
import uuid

from fastapi import FastAPI, Request, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel

app = FastAPI()

# --- AÑADE ESTE MIDDLEWARE ---
# Este middleware agregará el encabezado Permissions-Policy a cada respuesta.
class PermissionsPolicyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Llama a la siguiente parte de la cadena (tu ruta)
        response = await call_next(request)
        # Añade el encabezado antes de devolver la respuesta
        response.headers['Permissions-Policy'] = 'display-capture=(self)'
        return response


# -----------------------------

# --- MIDDLEWARE DE CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://fastapi-ruleta-americana.onrender.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Añade el middleware a tu aplicación FastAPI
app.add_middleware(PermissionsPolicyMiddleware)

app.mount("/static", StaticFiles(directory="static"), name="static")

# --- CONFIGURACIÓN Y ESTADO GLOBAL ---
SECRET_KEY = "tu_clave_secreta_super_dificil"
STREAMER_USERNAME = "mario"
streamer_ws: Optional[WebSocket] = None
streamer_is_active = False

# --- MODELOS PYDANTIC ---
class Event(BaseModel):
    content: str

class NumeroCaido(BaseModel):
    numero: int

class ConsecutiveData(BaseModel):
    consecutive_id: int

class DateTimeData(BaseModel):
    fecha_hora_str: str

# --- CONNECTION MANAGER ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.streamer_id: Optional[str] = None

    async def connect(self, websocket: WebSocket) -> str:
        await websocket.accept()
        client_id = str(uuid.uuid4())
        self.active_connections[client_id] = websocket
        print(f"Nuevo cliente conectado con ID: {client_id}")
        return client_id

    def disconnect(self, client_id: str):
        global streamer_ws, streamer_is_active
        if client_id in self.active_connections:
            del self.active_connections[client_id]
        
        if client_id == self.streamer_id:
            print("El streamer se ha desconectado.")
            streamer_ws = None
            streamer_is_active = False
            self.streamer_id = None
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.broadcast_json({"type": "stream_ended"}))

    async def broadcast_json(self, data: dict):
        message = json.dumps(data)
        for client_id, connection in list(self.active_connections.items()):
            try:
                await connection.send_text(message)
            except:
                self.disconnect(client_id)
    
    async def send_personal_json(self, data: dict, client_id: str):
        if client_id in self.active_connections:
            try:
                await self.active_connections[client_id].send_text(json.dumps(data))
            except:
                self.disconnect(client_id)

manager = ConnectionManager()

# --- ENDPOINTS HTTP ---
@app.get("/old")
async def get_old_version():
    return FileResponse(os.path.join(os.path.dirname(__file__), "index3.html"))

@app.get("/")
async def get_new_version():
    return FileResponse(os.path.join(os.path.dirname(__file__), "index4.html"))

@app.post("/stream_ended")
async def notify_stream_ended(x_secret_key: Optional[str] = Header(None)):
    if x_secret_key != SECRET_KEY:
        raise HTTPException(status_code=403, detail="Clave secreta inválida")
    if manager.streamer_id:
        manager.disconnect(manager.streamer_id)
    return {"status": "ok"}

# ... (otros endpoints HTTP sin cambios)

# --- ENDPOINT WEBSOCKET ---
@app.websocket("/ws/users")
async def websocket_users(websocket: WebSocket):
    global streamer_ws, streamer_is_active
    client_id = await manager.connect(websocket)
    
    try:
        while True:
            data_str = await websocket.receive_text()
            data = json.loads(data_str)
            msg_type = data.get("type")
            print(f"Mensaje recibido de {client_id}: {data}")  # Depuración

            if msg_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue

            elif msg_type == "login":
                username = data.get("name", "").lower()
                if username == STREAMER_USERNAME and not manager.streamer_id:
                    manager.streamer_id = client_id
                    streamer_ws = websocket
                    await manager.send_personal_json({
                        "type": "login_success",
                        "role": "streamer",
                        "streamer_id": client_id,
                        "client_id": client_id
                    }, client_id)
                    print(f"Usuario '{username}' ({client_id}) es ahora el Streamer.")
                else:
                    await manager.send_personal_json({
                        "type": "login_success",
                        "role": "viewer",
                        "streamer_id": manager.streamer_id or '',
                        "client_id": client_id
                    }, client_id)
                    if streamer_is_active:
                        await manager.send_personal_json({"type": "stream_started"}, client_id)

            elif msg_type == "offer":
                if manager.streamer_id:
                    print(f"Reenviando oferta de {client_id} a streamer {manager.streamer_id}")
                    data['from_id'] = client_id
                    await manager.send_personal_json(data, manager.streamer_id)

            elif msg_type == "answer":
                target_id = data.get("target_id")
                if target_id:
                    print(f"Reenviando respuesta de {client_id} a {target_id}")
                    data['from_id'] = client_id
                    await manager.send_personal_json(data, target_id)

            elif msg_type == "candidate":
                target_id = data.get("target_id")
                if target_id:
                    print(f"Reenviando candidato de {client_id} a {target_id}")
                    data['from_id'] = client_id
                    await manager.send_personal_json(data, target_id)

            elif msg_type == "start_stream":
                if client_id == manager.streamer_id:
                    streamer_is_active = True
                    await manager.broadcast_json({"type": "stream_started"})
                    print("El streamer ha iniciado la transmisión.")
            
            elif msg_type == "stream_ended":
                if client_id == manager.streamer_id:
                    manager.disconnect(client_id)

    except WebSocketDisconnect:
        print(f"Cliente {client_id} desconectado.")
        manager.disconnect(client_id)
    except (ValueError, json.JSONDecodeError):
        print(f"Error de formato de mensaje del cliente {client_id}")
"""

import asyncio
import json
import logging
import os
from typing import cast, Dict, List, Optional
import uuid

from fastapi import FastAPI, Request, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel

app = FastAPI()

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- MIDDLEWARE DE PERMISOS ---
class PermissionsPolicyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers['Permissions-Policy'] = 'display-capture=(self)'
        return response

# --- MIDDLEWARE DE CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://fastapi-ruleta-americana.onrender.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Añadir el middleware de permisos
app.add_middleware(PermissionsPolicyMiddleware)

# Montar directorio estático
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- CONFIGURACIÓN Y ESTADO GLOBAL ---
SECRET_KEY = "tu_clave_secreta_super_dificil"
STREAMER_USERNAME = "mario"
streamer_ws: Optional[WebSocket] = None
streamer_is_active = False

# --- MODELOS PYDANTIC ---
class Event(BaseModel):
    content: str

class NumeroCaido(BaseModel):
    numero: int

class ConsecutiveData(BaseModel):
    consecutive_id: int

class DateTimeData(BaseModel):
    fecha_hora_str: str

# --- CONNECTION MANAGER ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.streamer_id: Optional[str] = None

    async def connect(self, websocket: WebSocket) -> str:
        await websocket.accept()
        client_id = str(uuid.uuid4())
        self.active_connections[client_id] = websocket
        logger.info(f"Nuevo cliente conectado con ID: {client_id}")
        return client_id

    def disconnect(self, client_id: str):
        global streamer_ws, streamer_is_active
        if client_id in self.active_connections:
            del self.active_connections[client_id]
        
        if client_id == self.streamer_id:
            logger.info("El streamer se ha desconectado.")
            streamer_ws = None
            streamer_is_active = False
            self.streamer_id = None
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.broadcast_json({"type": "stream_ended"}))

    async def broadcast_json(self, data: dict):
        message = json.dumps(data)
        for client_id, connection in list(self.active_connections.items()):
            try:
                await connection.send_text(message)
            except:
                self.disconnect(client_id)
    
    async def send_personal_json(self, data: dict, client_id: str):
        if client_id in self.active_connections:
            try:
                await self.active_connections[client_id].send_text(json.dumps(data))
            except:
                self.disconnect(client_id)

manager = ConnectionManager()

# --- ENDPOINTS HTTP ---
@app.get("/old")
async def get_old_version():
    return FileResponse(os.path.join(os.path.dirname(__file__), "index3.html"))

@app.get("/")
async def get_new_version():
    return FileResponse(os.path.join(os.path.dirname(__file__), "index.html"))

@app.post("/fecha_hora/")
async def recibir_fecha_hora(data: DateTimeData):
    payload = {"type": "fecha_hora", "payload": data.fecha_hora_str}
    await manager.broadcast_to_users(json.dumps(payload))
    return {"status": "ok"}

@app.post("/stream_ended")
async def notify_stream_ended(x_secret_key: Optional[str] = Header(None)):
    if x_secret_key != SECRET_KEY:
        raise HTTPException(status_code=403, detail="Clave secreta inválida")
    if manager.streamer_id:
        manager.disconnect(manager.streamer_id)
    return {"status": "ok"}

# --- ENDPOINT WEBSOCKET ---
@app.websocket("/ws/users")
async def websocket_users(websocket: WebSocket):
    global streamer_ws, streamer_is_active
    client_id = await manager.connect(websocket)
    
    try:
        while True:
            data_str = await websocket.receive_text()
            data = json.loads(data_str)
            msg_type = data.get("type")
            logger.info(f"Mensaje recibido de {client_id}: {data}")

            if msg_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue

            elif msg_type == "login":
                username = data.get("name", "").lower()
                if username == STREAMER_USERNAME and not manager.streamer_id:
                    manager.streamer_id = client_id
                    streamer_ws = websocket
                    logger.info(f"Asignando streamer_id: {client_id} para usuario {username}")
                    await manager.send_personal_json({
                        "type": "login_success",
                        "role": "streamer",
                        "streamer_id": client_id,
                        "client_id": client_id
                    }, client_id)
                else:
                    await manager.send_personal_json({
                        "type": "login_success",
                        "role": "viewer",
                        "streamer_id": manager.streamer_id or '',
                        "client_id": client_id
                    }, client_id)
                    if streamer_is_active:
                        await manager.send_personal_json({"type": "stream_started"}, client_id)

            elif msg_type == "offer":
                if manager.streamer_id:
                    logger.info(f"Reenviando oferta de {client_id} a streamer {manager.streamer_id}")
                    data['from_id'] = client_id
                    await manager.send_personal_json(data, manager.streamer_id)

            elif msg_type == "answer":
                target_id = data.get("target_id")
                if target_id:
                    logger.info(f"Reenviando respuesta de {client_id} a {target_id}")
                    data['from_id'] = client_id
                    await manager.send_personal_json(data, target_id)

            elif msg_type == "candidate":
                target_id = data.get("target_id")
                if target_id:
                    logger.info(f"Reenviando candidato de {client_id} a {target_id}")
                    data['from_id'] = client_id
                    await manager.send_personal_json(data, target_id)

            elif msg_type == "start_stream":
                if client_id == manager.streamer_id:
                    streamer_is_active = True
                    await manager.broadcast_json({"type": "stream_started"})
                    logger.info("El streamer ha iniciado la transmisión.")
            
            elif msg_type == "stream_ended":
                if client_id == manager.streamer_id:
                    manager.disconnect(client_id)

    except WebSocketDisconnect:
        logger.info(f"Cliente {client_id} desconectado.")
        manager.disconnect(client_id)
    except (ValueError, json.JSONDecodeError):
        logger.error(f"Error de formato de mensaje del cliente {client_id}")