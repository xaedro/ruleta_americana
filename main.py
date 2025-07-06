# main.py

"""
import asyncio
import base64  # <--- Importación clave para la nueva lógica
import json
import logging
import os
from typing import cast, List, Optional

from fastapi import (FastAPI, File, Header, HTTPException, UploadFile,
                     WebSocket, WebSocketDisconnect)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

# --- MIDDLEWARE DE CORS (Esencial) ---
app = FastAPI()

origins = [
    "https://fastapi-ruleta-americana.onrender.com",
    "http://localhost",
    "http://localhost:8000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- FILTRADO DE LOGS (Tu código está perfecto aquí) ---
class EndpointFilter(logging.Filter):
    def __init__(self, paths_to_filter: List[str], *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._paths = paths_to_filter

    def filter(self, record: logging.LogRecord) -> bool:
        if len(record.args) >= 3:
            path = cast(str, record.args[2])
            for p in self._paths:
                if path.startswith(p):
                    return False
        return True

paths_to_silence = ["/upload"] # Ya no necesitamos silenciar /live.jpg o /live_data
uvicorn_access_logger = logging.getLogger("uvicorn.access")
uvicorn_access_logger.addFilter(EndpointFilter(paths_to_filter=paths_to_silence))


# --- CONFIGURACIÓN Y MODELOS ---
SECRET_KEY = "tu_clave_secreta_super_dificil"

class Event(BaseModel): content: str
class NumeroCaido(BaseModel): numero: int
class ConsecutiveData(BaseModel): consecutive_id: int
class DateTimeData(BaseModel): fecha_hora_str: str

# --- CONNECTION MANAGER (Modificado para transmitir JSON) ---
class ConnectionManager:
    def __init__(self):
        self.user_connections: List[WebSocket] = []
        self.blender_connections: List[WebSocket] = []
        self.apuestas_usuarios = {}

    async def connect_user(self, websocket: WebSocket):
        await websocket.accept()
        self.user_connections.append(websocket)
        await self.broadcast_to_users_text(f"Usuarios conectados: {len(self.user_connections)}")

    def disconnect_user(self, websocket: WebSocket):
        if websocket in self.user_connections:
            self.user_connections.remove(websocket)
        if websocket in self.apuestas_usuarios:
            del self.apuestas_usuarios[websocket]

    async def broadcast_to_users_text(self, message: str):
        for connection in self.user_connections:
            try:
                await connection.send_text(message)
            except:
                self.disconnect_user(connection)

    async def broadcast_to_users_json(self, data: dict):
        # Envía datos estructurados como una cadena JSON a todos los usuarios
        await self.broadcast_to_users_text(json.dumps(data))

    async def connect_blender(self, websocket: WebSocket):
        await websocket.accept()
        if not self.blender_connections:
            self.blender_connections.append(websocket)
        else:
            print("Advertencia: Se intentó conectar un segundo cliente de Blender. Conexión rechazada.")

    def disconnect_blender(self, websocket: WebSocket):
        if websocket in self.blender_connections:
            self.blender_connections.remove(websocket)

    async def send_to_blender(self, message: str):
        for connection in self.blender_connections:
            try:
                await connection.send_text(message)
            except:
                self.disconnect_blender(connection)
    
    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

manager = ConnectionManager()

# --- ENDPOINTS HTTP ---

# Endpoint para servir la nueva página principal
@app.get("/")
async def read_root():
    html_file_path = os.path.join(os.path.dirname(__file__), "index3.html")
    if os.path.exists(html_file_path):
        return FileResponse(html_file_path)
    else:
        raise HTTPException(status_code=404, detail="Archivo 'index3.html' no encontrado.")

# Endpoint de subida de imagen (LÓGICA PRINCIPAL NUEVA)
@app.post("/upload")
async def upload_image(file: UploadFile = File(...), x_secret_key: Optional[str] = Header(None)):
    if x_secret_key != SECRET_KEY:
        raise HTTPException(status_code=403, detail="Clave secreta inválida")

    # 1. Leer los bytes de la imagen
    image_bytes = await file.read()
    
    # 2. Codificar los bytes a Base64 y luego a texto (UTF-8)
    base64_image = base64.b64encode(image_bytes).decode("utf-8")
    
    # 3. Crear un payload JSON estructurado para el frame
    payload = {
        "type": "live_frame",
        "data": base64_image
    }
    
    # 4. Enviar el JSON a todos los usuarios conectados vía WebSocket
    await manager.broadcast_to_users_json(payload)
    
    return {"status": "ok", "message": "Imagen recibida y transmitida"}

# Conservamos tus otros endpoints HTTP sin cambios
@app.post("/send/")
async def receive_event(event: Event):
    await manager.broadcast_to_users_text(event.content)
    return {"status": "evento enviado"}

@app.post("/consecutivo_juego/")
async def recibir_consecutivo(data: ConsecutiveData):
    payload = {"type": "juego_numero", "payload": data.consecutive_id}
    await manager.broadcast_to_users_json(payload)
    return {"status": "ok"}

@app.post("/fecha_hora/")
async def recibir_fecha_hora(data: DateTimeData):
    payload = {"type": "fecha_hora", "payload": data.fecha_hora_str}
    await manager.broadcast_to_users_json(payload)
    return {"status": "ok"}

@app.post("/numero_caido/")
async def numero_caido(evento: NumeroCaido):
    numero_ganador = evento.numero
    await manager.broadcast_to_users_text(f"Número caído: {numero_ganador}")
    for ws, apuesta_info in list(manager.apuestas_usuarios.items()):
        if apuesta_info.get("apuesta") == numero_ganador:
            mensaje_ganador = f"¡Felicidades {apuesta_info.get('nombre')}, has ganado!"
            await manager.send_personal_message(mensaje_ganador, ws)
    return {"status": "mensaje enviado"}


# --- ENDPOINTS WEBSOCKET ---
@app.websocket("/ws/users")
async def websocket_users(websocket: WebSocket):
    await manager.connect_user(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                apuesta_data = json.loads(data)
                manager.apuestas_usuarios[websocket] = apuesta_data
                await manager.send_to_blender(f"apuesta:{data}")
                msg_confirmacion = f"Confirmación: tu apuesta por el número {apuesta_data.get('apuesta')} fue recibida"
                await manager.send_personal_message(msg_confirmacion, websocket)
            except (ValueError, json.JSONDecodeError):
                await manager.send_personal_message("Error: formato de apuesta inválido", websocket)
    except WebSocketDisconnect:
        manager.disconnect_user(websocket)
        await manager.broadcast_to_users_text(f"Usuarios conectados: {len(manager.user_connections)}")

@app.websocket("/ws/blender")
async def websocket_blender(websocket: WebSocket):
    await manager.connect_blender(websocket)
    print("--> Conexión WebSocket de Blender establecida.")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        print("--> Conexión WebSocket de Blender cerrada.")
        manager.disconnect_blender(websocket)

# --- NUEVO ENDPOINT PARA NOTIFICAR EL FIN DEL STREAM ---
@app.post("/stream_ended")
async def notify_stream_ended(x_secret_key: Optional[str] = Header(None)):
    # Usamos la misma clave secreta como una medida de seguridad simple
    if x_secret_key != SECRET_KEY:
        raise HTTPException(status_code=403, detail="Clave secreta inválida")
    
    # Preparamos el mensaje para los espectadores
    payload = {"type": "stream_ended"}
    
    # Lo enviamos a todos los usuarios conectados
    await manager.broadcast_to_users_json(payload)
    
    print("Notificación de fin de stream enviada a todos los espectadores.")
    return {"status": "ok", "message": "Notificación de fin de stream enviada."}
"""

# main.py
import asyncio
import base64
import json
import logging
import os
from typing import cast, List, Optional

from fastapi import (FastAPI, File, Header, HTTPException, UploadFile,
                     WebSocket, WebSocketDisconnect)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI()

# --- MIDDLEWARE DE CORS ---
origins = [
    "https://fastapi-ruleta-americana.onrender.com",
    "http://localhost",
    "http://localhost:8000",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- FILTRADO DE LOGS ---
class EndpointFilter(logging.Filter):
    def __init__(self, paths_to_filter: List[str], *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._paths = paths_to_filter

    def filter(self, record: logging.LogRecord) -> bool:
        if len(record.args) >= 3:
            path = cast(str, record.args[2])
            for p in self._paths:
                if path.startswith(p):
                    return False
        return True

paths_to_silence = ["/upload"]
uvicorn_access_logger = logging.getLogger("uvicorn.access")
uvicorn_access_logger.addFilter(EndpointFilter(paths_to_filter=paths_to_silence))

# --- CONFIGURACIÓN Y ESTADO GLOBAL ---
SECRET_KEY = "tu_clave_secreta_super_dificil"
STREAMER_USERNAME = "mario"
streamer_ws: Optional[WebSocket] = None
streamer_is_active = False

# --- MODELOS PYDANTIC ---
class Event(BaseModel): content: str
class NumeroCaido(BaseModel): numero: int
class ConsecutiveData(BaseModel): consecutive_id: int
class DateTimeData(BaseModel): fecha_hora_str: str

# --- CONNECTION MANAGER ---
class ConnectionManager:
    def __init__(self):
        self.user_connections: List[WebSocket] = []
        self.blender_connections: List[WebSocket] = []
        self.apuestas_usuarios = {}

    async def connect_user(self, websocket: WebSocket):
        await websocket.accept()
        self.user_connections.append(websocket)

    def disconnect_user(self, websocket: WebSocket):
        global streamer_ws, streamer_is_active
        if websocket in self.user_connections:
            self.user_connections.remove(websocket)
        
        if websocket == streamer_ws:
            print("El streamer se ha desconectado.")
            streamer_ws = None
            streamer_is_active = False
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.broadcast_to_users_json({"type": "stream_ended"}))

        if websocket in self.apuestas_usuarios:
            del self.apuestas_usuarios[websocket]

    async def broadcast_to_users_text(self, message: str):
        for connection in list(self.user_connections):
            try:
                await connection.send_text(message)
            except:
                self.disconnect_user(connection)

    async def broadcast_to_users_json(self, data: dict):
        await self.broadcast_to_users_text(json.dumps(data))
        
    async def connect_blender(self, websocket: WebSocket):
        await websocket.accept()
        if not self.blender_connections:
            self.blender_connections.append(websocket)
        else:
            print("Advertencia: Se intentó conectar un segundo cliente de Blender.")
    
    def disconnect_blender(self, websocket: WebSocket):
        if websocket in self.blender_connections:
            self.blender_connections.remove(websocket)

    async def send_to_blender(self, message: str):
        for connection in self.blender_connections:
            try:
                await connection.send_text(message)
            except:
                self.disconnect_blender(connection)
    
    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

manager = ConnectionManager()

# --- ENDPOINTS HTTP ---
@app.get("/")
async def read_root():
    html_file_path = os.path.join(os.path.dirname(__file__), "index3.html")
    if os.path.exists(html_file_path):
        return FileResponse(html_file_path)
    else:
        raise HTTPException(status_code=404, detail="Archivo 'index3.html' no encontrado.")

@app.post("/upload")
async def upload_image(file: UploadFile = File(...), x_secret_key: Optional[str] = Header(None)):
    if not streamer_is_active:
        raise HTTPException(status_code=403, detail="No hay ningún stream activo actualmente.")
    if x_secret_key != SECRET_KEY:
        raise HTTPException(status_code=403, detail="Clave secreta inválida")
    
    image_bytes = await file.read()
    base64_image = base64.b64encode(image_bytes).decode("utf-8")
    payload = {"type": "live_frame", "data": base64_image}
    await manager.broadcast_to_users_json(payload)
    return {"status": "ok"}

@app.post("/stream_ended")
async def notify_stream_ended(x_secret_key: Optional[str] = Header(None)):
    global streamer_ws, streamer_is_active
    
    if x_secret_key != SECRET_KEY:
        raise HTTPException(status_code=403, detail="Clave secreta inválida")
    
    streamer_ws = None
    streamer_is_active = False
    await manager.broadcast_to_users_json({"type": "stream_ended"})
    print("El stream ha finalizado por notificación.")
    return {"status": "ok"}

@app.post("/send/")
async def receive_event(event: Event):
    await manager.broadcast_to_users_text(event.content)
    return {"status": "evento enviado"}

@app.post("/consecutivo_juego/")
async def recibir_consecutivo(data: ConsecutiveData):
    await manager.broadcast_to_users_json({"type": "juego_numero", "payload": data.consecutive_id})
    return {"status": "ok"}

@app.post("/fecha_hora/")
async def recibir_fecha_hora(data: DateTimeData):
    await manager.broadcast_to_users_json({"type": "fecha_hora", "payload": data.fecha_hora_str})
    return {"status": "ok"}

@app.post("/numero_caido/")
async def numero_caido(evento: NumeroCaido):
    await manager.broadcast_to_users_text(f"Número caído: {evento.numero}")
    # Tu lógica de ganadores...
    return {"status": "mensaje enviado"}

# --- ENDPOINTS WEBSOCKET ---
@app.websocket("/ws/users")
async def websocket_users(websocket: WebSocket):
    global streamer_ws, streamer_is_active
    
    await manager.connect_user(websocket)
    try:
        while True:
            data_str = await websocket.receive_text()
            data = json.loads(data_str)
            msg_type = data.get("type")

            if msg_type == "login":
                
                username = data.get("name", "").lower()
                if username == STREAMER_USERNAME and streamer_ws is None:
                    streamer_ws = websocket
                    await websocket.send_text(json.dumps({"type": "login_success", "role": "streamer"}))
                    print(f"Usuario '{username}' ha iniciado sesión como Streamer.")
                else:
                    role = "viewer"
                    if username == STREAMER_USERNAME:
                        print(f"Usuario '{username}' intentó iniciar sesión, pero el rol ya está ocupado.")
                    await websocket.send_text(json.dumps({"type": "login_success", "role": role}))
                    await websocket.send_text(json.dumps({"type": "stream_status", "active": streamer_is_active}))

            elif msg_type == "start_stream":
                
                if websocket == streamer_ws and not streamer_is_active:
                    streamer_is_active = True
                    await manager.broadcast_to_users_json({"type": "stream_started"})
                    print("El streamer ha iniciado la transmisión.")

            elif "nombre" in data and "apuesta" in data:
                manager.apuestas_usuarios[websocket] = data
                await manager.send_to_blender(f"apuesta:{data_str}")
                msg_confirmacion = f"Confirmación: tu apuesta por el número {data.get('apuesta')} fue recibida"
                await manager.send_personal_message(msg_confirmacion, websocket)
                
    except WebSocketDisconnect:
        manager.disconnect_user(websocket)
        await manager.broadcast_to_users_text(f"Usuarios conectados: {len(manager.user_connections)}")
    except (ValueError, json.JSONDecodeError):
        await manager.send_personal_message("Error: formato de mensaje inválido", websocket)

@app.websocket("/ws/blender")
async def websocket_blender(websocket: WebSocket):
    await manager.connect_blender(websocket)
    try:
        while True: await websocket.receive_text()
    except WebSocketDisconnect: manager.disconnect_blender(websocket)