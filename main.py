# main.py
import asyncio
from fastapi import FastAPI, WebSocket, Request, Response, WebSocketDisconnect, HTTPException, UploadFile, File, Header
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
from typing import cast, List, Optional
import json
import os
import logging

# --- MIDDLEWARE DE CORS (¡¡¡LA PIEZA QUE FALTA MÁS IMPORTANTE!!!) ---
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

### 1. AÑADIR EL MIDDLEWARE DE CORS ###
# Esto es lo que soluciona el error "NetworkError when attempting to fetch resource".
# Permite que el JavaScript de tu página se comunique con el backend.
origins = [
    "https://fastapi-ruleta-americana.onrender.com",
    # Descomenta las siguientes líneas si pruebas en local
    # "http://localhost",
    # "http://localhost:8000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],  # Permite métodos como POST
    allow_headers=["*"],  # Permite cabeceras como X-Secret-Key
)
# --------------------------------------------------------------------

# --- FILTRADO DE LOGS (Tu código está perfecto aquí, sin cambios) ---
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

paths_to_silence = ["/live.jpg", "/upload", "/fecha_hora/"]
uvicorn_access_logger = logging.getLogger("uvicorn.access")
uvicorn_access_logger.addFilter(EndpointFilter(paths_to_filter=paths_to_silence))


# --- CONFIGURACIÓN Y VARIABLES GLOBALES ---
SECRET_KEY = "tu_clave_secreta_super_dificil"  # Definida una sola vez
latest_image_bytes = None # Variable para guardar la imagen en memoria

# --- MODELOS PYDANTIC (Sin cambios) ---
class Event(BaseModel): content: str
class NumeroCaido(BaseModel): numero: int
class ConsecutiveData(BaseModel): consecutive_id: int
class DateTimeData(BaseModel): fecha_hora_str: str

# --- CONNECTION MANAGER (Sin cambios, tu lógica es sólida) ---
class ConnectionManager:
    def __init__(self):
        self.user_connections: List[WebSocket] = []
        self.blender_connections: List[WebSocket] = []
        self.apuestas_usuarios = {}

    async def connect_user(self, websocket: WebSocket):
        await websocket.accept()
        self.user_connections.append(websocket)
        await self.broadcast_to_users("Usuarios conectados: {}".format(len(self.user_connections)))

    def disconnect_user(self, websocket: WebSocket):
        if websocket in self.user_connections: self.user_connections.remove(websocket)
        if websocket in self.apuestas_usuarios: del self.apuestas_usuarios[websocket]

    async def broadcast_to_users(self, message: str):
        for connection in self.user_connections:
            try: await connection.send_text(message)
            except: self.disconnect_user(connection)

    async def connect_blender(self, websocket: WebSocket):
        await websocket.accept()
        if not self.blender_connections: self.blender_connections.append(websocket)
        else: print("Advertencia: Se intentó conectar un segundo cliente de Blender. Conexión rechazada.")

    def disconnect_blender(self, websocket: WebSocket):
        if websocket in self.blender_connections: self.blender_connections.remove(websocket)

    async def send_to_blender(self, message: str):
        for connection in self.blender_connections:
            try: await connection.send_text(message)
            except: self.disconnect_blender(connection)
    
    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

manager = ConnectionManager()

# --- ENDPOINTS HTTP ---

# Endpoint para servir la página HTML (Tu código está perfecto aquí)
@app.get("/")
async def read_root():
    file_to_serve = "index2.html"
    html_file_path = os.path.join(os.path.dirname(__file__), file_to_serve)
    if os.path.exists(html_file_path):
        return FileResponse(html_file_path)
    else:
        raise HTTPException(status_code=404, detail=f"Archivo '{file_to_serve}' no encontrado.")

### 2. CORREGIR EL ENDPOINT DE SUBIDA DE IMAGEN ###
# Añadimos 'x_secret_key' como un Header para que FastAPI lo reconozca.
@app.post("/upload")
async def upload_image(file: UploadFile = File(...), x_secret_key: Optional[str] = Header(None)):
    global latest_image_bytes
    if x_secret_key != SECRET_KEY:
        raise HTTPException(status_code=403, detail="Clave secreta inválida")
    
    latest_image_bytes = await file.read()
    return {"message": "Imagen recibida"}

# Endpoint para ver la imagen en vivo (Tu código está perfecto aquí)
@app.get("/live.jpg")
async def get_live_image():
    if latest_image_bytes is None:
        raise HTTPException(status_code=404, detail="No hay imagen disponible")
    return Response(content=latest_image_bytes, media_type="image/jpeg")

# Otros endpoints (Sin cambios)
@app.post("/send/")
async def receive_event(event: Event):
    await manager.broadcast_to_users(event.content)
    return {"status": "evento enviado"}

# ... (El resto de tus endpoints HTTP no necesitan cambios) ...
@app.post("/consecutivo_juego/")
async def recibir_consecutivo(data: ConsecutiveData):
    payload = {"type": "juego_numero", "payload": data.consecutive_id}
    await manager.broadcast_to_users(json.dumps(payload))
    return {"status": "ok"}

@app.post("/fecha_hora/")
async def recibir_fecha_hora(data: DateTimeData):
    payload = {"type": "fecha_hora", "payload": data.fecha_hora_str}
    await manager.broadcast_to_users(json.dumps(payload))
    return {"status": "ok"}

@app.post("/numero_caido/")
async def numero_caido(evento: NumeroCaido):
    # ... tu lógica aquí ...
    return {"status": "mensaje enviado"}

# --- ENDPOINTS WEBSOCKET ---

### 3. MEJORAR EL WEBSOCKET PARA EL "KICKSTART" DEL FRONTEND ###
@app.websocket("/ws/users")
async def websocket_users(websocket: WebSocket):
    await manager.connect_user(websocket)
    
    ### AÑADIR ESTA LÍNEA ###
    # Envía un mensaje inicial para que el frontend sepa que la conexión está lista.
    # Esto soluciona el problema de que la página se quede en "cargando".
    await websocket.send_text(json.dumps({"type": "status", "payload": "Conexión establecida"}))
    
    try:
        while True:
            data = await websocket.receive_text()
            try:
                apuesta_data = json.loads(data)
                manager.apuestas_usuarios[websocket] = apuesta_data
                await manager.send_to_blender(f"apuesta:{data}")
                await manager.send_personal_message(f"Confirmación: tu apuesta por el número {apuesta_data.get('apuesta')} fue recibida", websocket)
            except (ValueError, json.JSONDecodeError):
                await manager.send_personal_message("Error: formato de apuesta inválido", websocket)
    except WebSocketDisconnect:
        manager.disconnect_user(websocket)
        await manager.broadcast_to_users(f"Usuarios conectados: {len(manager.user_connections)}")

# Endpoint de Blender (Sin cambios, está perfecto)
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